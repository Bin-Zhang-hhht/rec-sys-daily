from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from recsys_daily.collect import Candidate
from recsys_daily.content import BlogFeedCache
from recsys_daily.deep_read import DeepReadServices, deep_read, deep_read_blog, deep_read_paper
from recsys_daily.mineru import MinerUError


ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 10, tzinfo=UTC)

PAPER_HTML = "<html><body><article><h1>Two-Tower Retrieval</h1><h2>Method</h2><p>Two towers retrieve content.</p></article></body></html>"
ARTICLE_HTML = "<html><body><article><h1>Feed Ranking</h1><h2>Architecture</h2><p>Bounded article content.</p></article></body></html>"


def _paper() -> Candidate:
    return Candidate(
        kind="paper",
        source_id="arxiv",
        title="Two-Tower Retrieval for Content Recommendation",
        url="https://arxiv.org/abs/2608.01234",
        published_at=NOW,
        authors=("Ada Lovelace",),
        excerpt="An abstract fallback that must not be persisted.",
        arxiv_id="2608.01234",
    )


def _blog() -> Candidate:
    return Candidate(
        kind="blog",
        source_id="example",
        title="How We Improved Feed Ranking",
        url="https://engineering.example.com/posts/feed-ranking",
        published_at=NOW,
        authors=("Example Engineer",),
        excerpt="Short feed excerpt that must not be persisted.",
    )


def _paper_analysis(*_args: object) -> dict[str, object]:
    return {
        "problem_zh": "解决内容推荐中的候选召回问题。",
        "contributions_zh": ["提出双塔召回模型。"],
        "method_zh": "使用独立的用户和内容编码器。",
        "experiments": {"datasets": ["FixtureSet"], "metrics": ["Recall@20"]},
        "evidence_refs": [{"section": "Method", "page": 2}],
    }


def _blog_analysis(*_args: object) -> dict[str, object]:
    return {
        "system_context_zh": "内容流排序服务。",
        "architecture_zh": "候选召回后进行排序。",
        "implementation_zh": "通过离线特征和在线服务协同实现。",
        "evidence_refs": [{"heading": "Architecture"}],
    }


class FakeContent:
    def __init__(self, tmp_path: Path, *, html: str | None = None, article: str | None = None, pages: list[int] | None = None) -> None:
        self.tmp_path = tmp_path
        self.html = html
        self.article = article
        self.pages = pages or []
        self.pdf_calls = 0
        self.article_calls = 0

    def fetch_bytes(self, _url: str, _limit: int) -> bytes:
        self.pdf_calls += 1
        return b"fixture pdf"

    def feed_content(self, _candidate: Candidate) -> str | None:
        return None

    def fetch_article_html(self, _candidate: Candidate) -> str:
        self.article_calls += 1
        if self.article is None:
            raise RuntimeError("article unavailable")
        return self.article

    def extract_article(self, html: str) -> str:
        return "Architecture\n" + html.replace("<", " ").replace(">", " ")


class FakeMinerU:
    max_pdf_bytes = 20 * 1024 * 1024

    def __init__(self, body: str | Exception = "Full MinerU paper body") -> None:
        self.body = body
        self.calls: list[tuple[bytes, str, Path]] = []

    def parse_pdf(self, pdf: bytes, filename: str, temporary_root: Path) -> str:
        self.calls.append((pdf, filename, temporary_root))
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def _services(tmp_path: Path, content: FakeContent, *, mineru=None, text=_paper_analysis) -> DeepReadServices:
    return DeepReadServices(
        content=content,
        temporary_root=tmp_path,
        text_reader=text,
        mineru=mineru or FakeMinerU(),
        max_html_bytes=5 * 1024 * 1024,
    )


def test_paper_uses_pdf_and_mineru_only(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, html=PAPER_HTML, pages=[2, 5])
    mineru = FakeMinerU()
    services = _services(tmp_path, content, mineru=mineru)

    reading = deep_read_paper(_paper(), services)

    assert reading.analysis_basis == "mineru_full_text"
    assert mineru.calls == [(b"fixture pdf", "arxiv-2608.01234.pdf", tmp_path)]
    assert list(tmp_path.iterdir()) == []


def test_paper_does_not_call_visual_page_detection(tmp_path: Path) -> None:
    content = FakeContent(tmp_path)
    assert not hasattr(content, "critical_pages")
    assert not hasattr(content, "render_pages")

    reading = deep_read_paper(_paper(), _services(tmp_path, content))

    assert reading.analysis_basis == "mineru_full_text"
    assert list(tmp_path.iterdir()) == []


def test_paper_rejects_empty_model_analysis(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, html=PAPER_HTML)
    services = _services(tmp_path, content, text=lambda *_args: {})

    with pytest.raises(ValueError, match="meaningful|problem"):
        deep_read_paper(_paper(), services)


def test_blog_rejects_empty_model_analysis(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, article=ARTICLE_HTML)
    services = _services(tmp_path, content, text=lambda *_args: {})

    with pytest.raises(ValueError, match="meaningful|system_context"):
        deep_read_blog(_blog(), services)


def test_paper_mineru_failure_uses_abstract_fallback(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, html="<html><script>alert('ignore')</script></html>")
    observed: list[tuple[str, str]] = []
    services = _services(
        tmp_path,
        content,
        mineru=FakeMinerU(MinerUError("parse failed")),
        text=lambda _kind, body, context: observed.append((body, context["analysis_basis"])) or _paper_analysis(),
    )

    reading = deep_read_paper(_paper(), services)

    assert reading.analysis_basis == "abstract_fallback"
    assert observed == [(_paper().excerpt, "abstract_fallback")]
    assert list(tmp_path.iterdir()) == []


def test_blog_falls_back_from_article_to_excerpt_and_cleans_raw_html_on_text_failure(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, article=ARTICLE_HTML)
    services = _services(tmp_path, content, text=lambda *_args: (_ for _ in ()).throw(RuntimeError("text unavailable")))

    with pytest.raises(RuntimeError, match="text unavailable"):
        deep_read_blog(_blog(), services)

    assert content.article_calls == 1
    assert list(tmp_path.iterdir()) == []


def test_blog_uses_feed_content_before_article_and_directory_artifact_excludes_raw_source(tmp_path: Path) -> None:
    content = FakeContent(tmp_path)
    content.feed_content = lambda _candidate: "Full feed content that must not be persisted."  # type: ignore[method-assign]
    services = _services(tmp_path, content, text=_blog_analysis)
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "blog-candidates.json").write_text(json.dumps([{
        "kind": "blog", "source_id": "example", "title": _blog().title, "url": _blog().url,
        "published_at": "2026-08-10T00:00:00Z", "authors": ["Example Engineer"],
        "excerpt": _blog().excerpt,
    }]), encoding="utf-8")

    deep_read("blog", input_dir, output_dir, services=services)

    payload = (output_dir / "blog-deep-readings.json").read_text(encoding="utf-8")
    assert "rss_full_content" in payload
    assert "Full feed content" not in payload
    assert "Short feed excerpt" not in payload
    assert content.article_calls == 0


def test_blog_article_fetch_uses_configured_html_limit_without_outer_limiter(tmp_path: Path) -> None:
    content = FakeContent(tmp_path, article="<p>Article body</p>")
    calls: list[tuple[object, int]] = []
    content.fetch_article_html = lambda candidate, limit: calls.append((candidate, limit)) or "<p>Article body</p>"  # type: ignore[method-assign]
    services = _services(tmp_path, content, text=_blog_analysis)
    services.max_html_bytes = 123

    reading = deep_read_blog(_blog(), services)

    assert reading.analysis_basis == "article_html"
    assert calls == [(_blog(), 123)]


def test_deep_read_caps_input_to_top_sixteen(tmp_path: Path) -> None:
    content = FakeContent(tmp_path)
    services = _services(tmp_path, content, text=_blog_analysis)
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    candidates = [
        {
            "kind": "blog",
            "source_id": "example",
            "title": f"Feed Ranking {index}",
            "url": f"https://engineering.example.com/posts/{index}",
            "published_at": "2026-08-10T00:00:00Z",
            "authors": ["Example Engineer"],
            "excerpt": "Short excerpt",
        }
        for index in range(20)
    ]
    (input_dir / "blog-candidates.json").write_text(json.dumps(candidates), encoding="utf-8")

    deep_read("blog", input_dir, output_dir, services=services)

    payload = json.loads((output_dir / "blog-deep-readings.json").read_text(encoding="utf-8"))
    assert len(payload["items"]) == 16


def test_blog_deep_read_fetches_each_source_feed_once_and_reuses_content(tmp_path: Path) -> None:
    payload = """
    <rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><item>
      <guid>feed-ranking</guid><title>How We Improved Feed Ranking</title>
      <link>https://engineering.example.com/posts/feed-ranking</link>
      <pubDate>Mon, 10 Aug 2026 00:00:00 +0000</pubDate>
      <content:encoded><![CDATA[<p>Full feed implementation.</p>]]></content:encoded>
    </item></channel></rss>
    """
    calls: list[tuple[str, str]] = []
    cache = BlogFeedCache(
        source_urls={"example": "https://example.test/feed"},
        fetch_feed=lambda source_id, url: calls.append((source_id, url)) or payload,
    )
    content = FakeContent(tmp_path)
    services = _services(tmp_path, content, text=_blog_analysis)
    services.blog_feed_content = cache.get

    assert deep_read_blog(_blog(), services).analysis_basis == "rss_full_content"
    assert deep_read_blog(_blog(), services).analysis_basis == "rss_full_content"
    assert calls == [("example", "https://example.test/feed")]


def test_blog_second_feed_failure_uses_article_or_excerpt(tmp_path: Path) -> None:
    cache = BlogFeedCache(
        source_urls={"example": "https://example.test/feed"},
        fetch_feed=lambda *_args: (_ for _ in ()).throw(RuntimeError("feed down")),
    )
    content = FakeContent(tmp_path, article="<p>Article fallback</p>")
    services = _services(tmp_path, content, text=_blog_analysis)
    services.blog_feed_content = cache.get

    reading = deep_read_blog(_blog(), services)

    assert reading.analysis_basis == "article_html"


def test_blog_second_feed_failure_can_be_retried(tmp_path: Path) -> None:
    payload = """
    <rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'><channel><item>
      <guid>feed-ranking</guid><title>How We Improved Feed Ranking</title>
      <link>https://engineering.example.com/posts/feed-ranking</link>
      <pubDate>Mon, 10 Aug 2026 00:00:00 +0000</pubDate>
      <content:encoded><![CDATA[<p>Full feed implementation.</p>]]></content:encoded>
    </item></channel></rss>
    """
    attempts = 0

    def fetch_feed(_source_id: str, _url: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("feed temporarily down")
        return payload

    cache = BlogFeedCache(
        source_urls={"example": "https://example.test/feed"},
        fetch_feed=fetch_feed,
    )

    assert cache.get(_blog()) is None
    assert cache.get(_blog()) == "<p>Full feed implementation.</p>"
    assert attempts == 2
