from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path
import json
import socket

import pytest
import requests

from recsys_daily import collect
from recsys_daily.collect import Candidate, CollectionResult, FeedResponse, _arxiv_url, _entry_feed_content, collect_candidates, parse_blog_feed, stable_id
from recsys_daily.config import SourcesConfig, load_config
from recsys_daily.metadata import MetadataResult
from recsys_daily.security import PublicUrlError, fetch_public_url, validate_public_url
from recsys_daily.schemas import SourceState, State
from recsys_daily.stage_one import collection_stage_report, load_history_ids, run_collect_filter
from recsys_daily.testing_fixtures import _fixture_metadata_candidate_ids
from recsys_daily.state import compute_query_windows, query_window


ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)

ARXIV_ATOM = """<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/2608.01234v2</id><updated>2026-08-09T12:00:00Z</updated><published>2026-08-09T12:00:00Z</published><title>Two-Tower Retrieval for Content Recommendation</title><summary>We study candidate retrieval for content recommendation with a Two-Tower Model.</summary><author><name>Ada Lovelace</name></author><author><name>Grace Hopper</name></author><link href='https://arxiv.org/abs/2608.01234v2' rel='alternate'/><category term='cs.IR'/></entry></feed>"""
BLOG_RSS = """<?xml version='1.0'?><rss version='2.0'><channel><item><guid>example-ranking-2026</guid><title>How We Improved Feed Ranking</title><link>https://engineering.example.com/posts/feed-ranking?utm_source=rss</link><pubDate>Sun, 09 Aug 2026 08:30:00 +0000</pubDate><description>Practical feed ranking lessons.</description><author>engineer@example.com (Example Engineer)</author></item></channel></rss>"""


def _public_resolver(host: str, port: int, *_args: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _config():
    config = load_config(ROOT)
    sources = SourcesConfig(academic=config.sources.academic, blogs=[config.sources.blogs[0]])
    return config.model_copy(update={"sources": sources})


def test_query_window_uses_cold_start_and_incremental_offsets() -> None:
    cold = compute_query_windows(None, now=NOW)
    state = State(last_success_at=datetime(2026, 8, 9, 0, 0, tzinfo=UTC))
    incremental = query_window(state, now=NOW)

    assert cold.papers_since == datetime(2021, 8, 10, 0, 0, tzinfo=UTC)
    assert cold.blogs_since == datetime(2023, 8, 10, 0, 0, tzinfo=UTC)
    assert cold.paper_start == cold.papers_since
    assert cold.blog_start == cold.blogs_since
    assert incremental.papers_since == datetime(2026, 8, 7, 0, 0, tzinfo=UTC)
    assert incremental.blogs_since == datetime(2026, 8, 2, 0, 0, tzinfo=UTC)


def test_query_window_treats_invalid_state_as_cold_start() -> None:
    window = query_window({"last_success_at": "not-a-timestamp"}, now=NOW)

    assert window.papers_since == datetime(2021, 8, 10, 0, 0, tzinfo=UTC)


def test_stable_id_prefers_arxiv_doi_url_then_title() -> None:
    base = Candidate(
        kind="paper",
        source_id="arxiv",
        title="  A   Retrieval Paper ",
        url="https://example.com/paper?utm_source=test",
        published_at=NOW,
        authors=("Ada",),
        excerpt="short abstract",
    )

    assert stable_id(replace(base, arxiv_id="arXiv:2608.01234v2")) == "arxiv-2608.01234"
    assert stable_id(replace(base, doi="https://doi.org/10.1234/Example.DOI")).startswith("doi-10.1234-")
    assert stable_id(base).startswith("url-")
    assert stable_id(replace(base, url=None)).startswith("title-")


def test_arxiv_query_encodes_submitted_date_window() -> None:
    config = load_config(ROOT)
    url = _arxiv_url(config, query_window(None, now=NOW))
    assert "submittedDate" in url
    assert "from=" not in url


def test_stable_ids_deduplicate_only_identical_normalized_identity_keys() -> None:
    first = Candidate("blog", "example", "A similar title", "https://example.com/item?utm_source=rss", NOW)
    same_url = Candidate("blog", "other", "A different title", "https://example.com/item", NOW)
    similar_title = Candidate("blog", "example", "A similar title!", "https://example.com/another-item", NOW)

    assert stable_id(first) == stable_id(same_url)
    assert stable_id(first) != stable_id(similar_title)


def test_collect_normalizes_fixtures_and_honors_conditional_headers() -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fetcher(url: str, headers: dict[str, str]) -> FeedResponse:
        calls.append((url, headers))
        if "export.arxiv.org" in url:
            return FeedResponse(200, ARXIV_ATOM.encode(), {"ETag": '"arxiv-v1"'})
        return FeedResponse(200, BLOG_RSS.encode(), {"Last-Modified": "Sun, 09 Aug 2026 08:30:00 GMT"})

    state = State(sources={
        "arxiv": SourceState(etag='"old-arxiv"'),
        "meta_engineering": SourceState(last_modified="Sat, 08 Aug 2026 00:00:00 GMT"),
    })
    result = collect_candidates(_config(), state.model_dump(), now=NOW, fetcher=fetcher, resolver=_public_resolver)

    assert len(calls) == 2
    assert calls[0][1]["If-None-Match"] == '"old-arxiv"'
    assert calls[1][1]["If-Modified-Since"] == "Sat, 08 Aug 2026 00:00:00 GMT"
    assert {candidate.kind for candidate in result.candidates} == {"paper", "blog"}
    paper = next(candidate for candidate in result.candidates if candidate.kind == "paper")
    blog = next(candidate for candidate in result.candidates if candidate.kind == "blog")
    assert stable_id(paper) == "arxiv-2608.01234"
    assert paper.authors == ("Ada Lovelace", "Grace Hopper")
    assert blog.url == "https://engineering.example.com/posts/feed-ranking"
    assert len(blog.excerpt) <= _config().settings.storage.max_blog_excerpt_chars
    assert result.source_states["arxiv"].etag == '"arxiv-v1"'


def test_load_history_ids_combines_state_items_and_digests(tmp_path: Path) -> None:
    config = load_config(ROOT)
    item = tmp_path / "items/papers/2025/01/digest-paper.json"
    blog = tmp_path / "items/blogs/2025/01/digest-blog.json"
    digest = tmp_path / "digests/2025/01/2025-01-02.json"
    item.parent.mkdir(parents=True)
    blog.parent.mkdir(parents=True)
    digest.parent.mkdir(parents=True)
    item.write_text(json.dumps({
        "id": "digest-paper",
        "kind": "paper",
        "title": "History",
        "summary_zh": "Historical summary",
        "source": "arxiv",
        "url": "https://arxiv.org/abs/2501.00001",
        "published_at": "2025-01-02T00:00:00Z",
        "authors": ["Author"],
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {"analysis_basis": "abstract_fallback", "visual_analysis": {"status": "not_required"}},
    }), encoding="utf-8")
    blog.write_text(json.dumps({
        "id": "digest-blog",
        "kind": "blog",
        "title": "History Blog",
        "summary_zh": "Historical summary",
        "source": "meta_engineering",
        "url": "https://engineering.example.com/history",
        "published_at": "2025-01-02T00:00:00Z",
        "authors": ["Author"],
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {"analysis_basis": "excerpt_fallback", "system_context_zh": "context"},
    }), encoding="utf-8")
    digest.write_text(json.dumps({
        "date": "2025-01-02",
        "papers": [{"item_id": "digest-paper", "recommendation_reason_zh": "history", "rank": 1}],
        "blogs": [{"item_id": "digest-blog", "recommendation_reason_zh": "history", "rank": 1}],
    }), encoding="utf-8")

    assert load_history_ids(tmp_path, config, State(recommended_item_ids=["state-history"])) == {
        "state-history",
        "digest-paper",
        "digest-blog",
    }


@pytest.mark.parametrize(
    ("relative", "payload"),
    [
        ("items/papers/2025/01/broken.json", {"id": 42}),
        ("items/papers/2025/01/missing-fields.json", {"id": "missing-fields"}),
        ("digests/2025/01/2025-01-02.json", {"date": "2025-01-02", "papers": [{}], "blogs": []}),
    ],
)
def test_load_history_ids_rejects_corrupt_canonical_json(tmp_path: Path, relative: str, payload: object) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical history"):
        load_history_ids(tmp_path, load_config(ROOT), State())


def test_collection_stage_report_omits_disabled_sources() -> None:
    config = load_config(ROOT)
    disabled = config.sources.blogs[0].model_copy(update={"enabled": False})
    config = config.model_copy(update={"sources": SourcesConfig(academic=config.sources.academic, blogs=[disabled, *config.sources.blogs[1:]])})
    result = CollectionResult(window=query_window(None, now=NOW), candidates=[], source_states={})

    report = collection_stage_report(config, result, MetadataResult([], 0, 1.0, 0))

    assert disabled.id not in {source.source_id for source in report.sources}


def test_fixture_metadata_parser_supports_legacy_and_source_document_envelopes() -> None:
    legacy = [{"role": "user", "content": "id: legacy-id\ntitle: Legacy"}]
    structured = [
        {"role": "system", "content": "classify"},
        {"role": "user", "content": json.dumps({"task": "classify", "source_documents": [{"id": "structured-id"}]})},
    ]

    assert _fixture_metadata_candidate_ids(legacy) == ["legacy-id"]
    assert _fixture_metadata_candidate_ids(structured) == ["structured-id"]


def test_load_history_ids_rejects_duplicate_canonical_item_ids(tmp_path: Path) -> None:
    config = load_config(ROOT)
    item = {
        "id": "duplicate-id",
        "kind": "paper",
        "title": "History",
        "summary_zh": "Historical summary",
        "source": "arxiv",
        "url": "https://arxiv.org/abs/2501.00001",
        "published_at": "2025-01-02T00:00:00Z",
        "authors": ["Author"],
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {"analysis_basis": "abstract_fallback", "visual_analysis": {"status": "not_required"}},
    }
    first = tmp_path / "items/papers/2025/01/duplicate-id.json"
    second = tmp_path / "items/papers/2026/01/duplicate-id.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text(json.dumps(item), encoding="utf-8")
    second.write_text(json.dumps({**item, "published_at": "2026-01-02T00:00:00Z"}), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate canonical history item"):
        load_history_ids(tmp_path, config, State())


@pytest.mark.parametrize("digest", [
    {"date": "2025-01-02", "papers": [{"item_id": "missing-id", "recommendation_reason_zh": "history", "rank": 1}], "blogs": []},
    {"date": "2025-01-02", "papers": [], "blogs": [{"item_id": "paper-id", "recommendation_reason_zh": "history", "rank": 1}]},
])
def test_load_history_ids_rejects_missing_or_wrong_kind_digest_reference(tmp_path: Path, digest: dict[str, object]) -> None:
    config = load_config(ROOT)
    item = {
        "id": "paper-id",
        "kind": "paper",
        "title": "History",
        "summary_zh": "Historical summary",
        "source": "arxiv",
        "url": "https://arxiv.org/abs/2501.00001",
        "published_at": "2025-01-02T00:00:00Z",
        "authors": ["Author"],
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {"analysis_basis": "abstract_fallback", "visual_analysis": {"status": "not_required"}},
    }
    item_path = tmp_path / "items/papers/2025/01/paper-id.json"
    digest_path = tmp_path / "digests/2025/01/2025-01-02.json"
    item_path.parent.mkdir(parents=True)
    digest_path.parent.mkdir(parents=True)
    item_path.write_text(json.dumps(item), encoding="utf-8")
    digest_path.write_text(json.dumps(digest), encoding="utf-8")

    with pytest.raises(ValueError, match="digest reference"):
        load_history_ids(tmp_path, config, State(recommended_item_ids=["missing-id"]))


def test_run_collect_filter_uses_injected_transport_and_metadata_on_nonhistorical_candidates(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetcher(url: str, _headers: dict[str, str]) -> FeedResponse:
        calls.append(url)
        payload = ARXIV_ATOM if "export.arxiv.org" in url else BLOG_RSS.replace(
            "<rss version='2.0'>",
            "<rss version='2.0' xmlns:content='http://purl.org/rss/1.0/modules/content/'>",
        ).replace(
            "</item>",
            "<content:encoded><![CDATA[Full implementation details]]></content:encoded></item>",
        )
        return FeedResponse(200, payload.encode(), {})

    completed_ids: list[str] = []

    def complete_json(messages: list[dict[str, str]], _schema: object) -> dict[str, object]:
        ids = _fixture_metadata_candidate_ids(messages)
        completed_ids.extend(ids)
        return {"items": [{
            "id": item_id,
            "summary_zh": "fixture summary",
            "targets": ["content"],
            "scenarios": ["text_feed"],
            "tasks": ["retrieval"],
            "methods": ["two_tower"],
            "relevance_score": 0.9,
            "graph_relations": [],
            "degraded": False,
        } for item_id in ids]}

    run_collect_filter(
        _config(),
        tmp_path,
        State(),
        {"arxiv-2608.01234"},
        complete_json,
        fetcher=fetcher,
        resolver=_public_resolver,
        now=NOW,
        run_id="stage-one-test",
    )

    papers = (tmp_path / "papers.jsonl").read_text(encoding="utf-8")
    blogs = (tmp_path / "blogs.jsonl").read_text(encoding="utf-8")
    assert len(calls) == 2
    assert papers == ""
    assert len(completed_ids) == 1
    assert completed_ids[0] in blogs
    assert "Full implementation details" not in blogs
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))["run_id"] == "stage-one-test"
    assert set(json.loads((tmp_path / "source-states.json").read_text(encoding="utf-8"))) == {"arxiv", "meta_engineering"}


def test_collect_passes_configured_excerpt_limit() -> None:
    config = load_config(ROOT)
    storage = config.settings.storage.model_copy(update={"max_blog_excerpt_chars": 7})
    settings = config.settings.model_copy(update={"storage": storage})
    blog_source = config.sources.blogs[0].model_copy(update={"url": "https://example.test/feed"})
    academic = config.sources.academic[0].model_copy(update={"enabled": False})
    config = config.model_copy(update={"settings": settings, "sources": SourcesConfig(academic=[academic], blogs=[blog_source])})
    payload = b"""
    <rss version='2.0'><channel><item>
      <guid>short-feed-item</guid><title>Feed Ranking</title>
      <link>https://example.test/item</link>
      <pubDate>Mon, 10 Aug 2026 00:00:00 +0000</pubDate>
      <description>long excerpt value</description>
    </item></channel></rss>
    """

    def fetcher(_url: str, _headers: dict[str, str]) -> FeedResponse:
        return FeedResponse(200, payload, {})

    result = collect_candidates(config, now=NOW, fetcher=fetcher, resolver=_public_resolver)
    blog = next(item for item in result.candidates if item.kind == "blog")
    assert len(blog.excerpt) <= 7


def test_optional_feed_failure_is_a_warning_but_required_failure_stops_collection() -> None:
    def fail_fetcher(_url: str, _headers: dict[str, str]) -> FeedResponse:
        raise RuntimeError("offline")

    with pytest.raises(RuntimeError, match="arxiv"):
        collect_candidates(_config(), now=NOW, fetcher=fail_fetcher, resolver=_public_resolver)

    def arxiv_only(url: str, _headers: dict[str, str]) -> FeedResponse:
        if "export.arxiv.org" in url:
            return FeedResponse(200, ARXIV_ATOM.encode(), {})
        raise RuntimeError("offline")

    result = collect_candidates(_config(), now=NOW, fetcher=arxiv_only, resolver=_public_resolver)
    assert len(result.candidates) == 1
    assert result.warnings == ["meta_engineering: offline"]


def test_public_url_validation_rejects_non_public_addresses() -> None:
    with pytest.raises(PublicUrlError):
        validate_public_url("http://127.0.0.1/feed")
    with pytest.raises(PublicUrlError):
        validate_public_url("ftp://example.com/feed", resolver=_public_resolver)
    assert validate_public_url("https://public.example/feed", resolver=_public_resolver) == "https://public.example/feed"


def test_public_url_validation_wraps_malformed_urls() -> None:
    with pytest.raises(PublicUrlError):
        validate_public_url("http://[::1/feed")


def test_fetch_revalidates_every_redirect_target() -> None:
    class Redirect:
        status_code = 302
        is_redirect = True
        is_permanent_redirect = False
        headers = {"Location": "http://127.0.0.1/internal"}

    calls: list[str] = []

    def request(url: str, **_kwargs: object) -> Redirect:
        calls.append(url)
        return Redirect()

    with pytest.raises(PublicUrlError):
        fetch_public_url("https://public.example/feed", resolver=_public_resolver, request=request)
    assert calls == ["https://public.example/feed"]


def test_fetch_public_url_retries_transport_failures_and_sets_user_agent() -> None:
    class Response:
        status_code = 200
        content = b"ok"
        headers: dict[str, str] = {}
        is_redirect = False
        is_permanent_redirect = False

    calls: list[dict[str, object]] = []
    acquired = 0
    attempts = iter([requests.ConnectionError("tls eof"), Response()])

    def acquire() -> None:
        nonlocal acquired
        acquired += 1

    def request(_url: str, **kwargs: object) -> Response:
        calls.append(kwargs)
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    response = fetch_public_url(
        "https://public.example/feed",
        resolver=_public_resolver,
        request=request,
        max_attempts=2,
        user_agent="RecSysDaily/test",
        sleeper=lambda _: None,
        attempt_limiter=acquire,
        backoff_seconds=0.25,
        max_delay_seconds=4,
    )

    assert response.status_code == 200
    assert calls[0]["headers"] == {"User-Agent": "RecSysDaily/test"}
    assert len(calls) == 2
    assert acquired == 2


def test_fetch_public_url_preserves_explicit_user_agent() -> None:
    class Response:
        status_code = 200
        content = b"ok"
        headers: dict[str, str] = {}
        is_redirect = False
        is_permanent_redirect = False

    calls: list[dict[str, object]] = []

    def request(_url: str, **kwargs: object) -> Response:
        calls.append(kwargs)
        return Response()

    fetch_public_url(
        "https://public.example/feed",
        headers={"user-agent": "Explicit/2.0"},
        resolver=_public_resolver,
        request=request,
        user_agent="RecSysDaily/test",
    )

    assert calls[0]["headers"] == {"user-agent": "Explicit/2.0"}


def test_collect_retries_source_http_503_with_configured_network_options(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config()
    academic = config.sources.academic[0].model_copy(update={"enabled": False})
    blog = config.sources.blogs[0]
    config = config.model_copy(update={"sources": SourcesConfig(academic=[academic], blogs=[blog])})
    attempts = 0
    seen_headers: list[dict[str, str]] = []
    limiter_intervals: list[float] = []
    limiter_calls: list[str] = []

    class FakeDomainLimiter:
        def __init__(self, min_interval_seconds: float) -> None:
            limiter_intervals.append(min_interval_seconds)

        def acquire(self, url: str) -> None:
            limiter_calls.append(url)

    monkeypatch.setattr(collect, "DomainRateLimiter", FakeDomainLimiter)

    class Response:
        is_redirect = False
        is_permanent_redirect = False

        def __init__(self, status_code: int, content: bytes = b"") -> None:
            self.status_code = status_code
            self.content = content
            self.headers: dict[str, str] = {}
            self.url = blog.url

    def request(_url: str, **kwargs: object) -> Response:
        nonlocal attempts
        attempts += 1
        seen_headers.append(dict(kwargs["headers"]))  # type: ignore[arg-type]
        if attempts == 1:
            return Response(503)
        return Response(200, BLOG_RSS.encode())

    original_fetch = fetch_public_url

    def configured_fetch(url: str, **kwargs: object) -> requests.Response:
        assert kwargs["max_attempts"] == config.settings.limits.retry_attempts
        assert kwargs["user_agent"] == config.settings.request_user_agent
        assert kwargs["backoff_seconds"] == config.settings.limits.retry_backoff_seconds
        assert kwargs["max_delay_seconds"] == config.settings.limits.retry_max_delay_seconds
        return original_fetch(url, request=request, sleeper=lambda _: None, **kwargs)

    monkeypatch.setattr(collect, "fetch_public_url", configured_fetch)

    result = collect_candidates(config, now=NOW, resolver=_public_resolver)

    assert attempts == 2
    assert limiter_intervals == [
        config.settings.limits.arxiv_min_interval_seconds,
        config.settings.limits.blog_min_interval_seconds_per_domain,
    ]
    assert limiter_calls == [blog.url, blog.url]
    assert seen_headers[0]["User-Agent"] == config.settings.request_user_agent
    assert result.warnings == []


def test_blog_feed_preserves_content_encoded_for_full_reading() -> None:
    payload = """
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
      <channel><item>
        <guid>full-feed-1</guid>
        <title>Full Feed Article</title>
        <link>https://example.com/full-feed</link>
        <pubDate>Mon, 10 Aug 2026 00:00:00 +0000</pubDate>
        <description>Short excerpt</description>
        <content:encoded><![CDATA[<h1>Architecture</h1><p>Full implementation details.</p>]]></content:encoded>
      </item></channel>
    </rss>
    """

    candidates = parse_blog_feed(payload, source_id="example")

    assert candidates[0].feed_content == "<h1>Architecture</h1><p>Full implementation details.</p>"


def test_feed_content_falls_through_empty_atom_content_to_encoded_value() -> None:
    assert _entry_feed_content({"content": [{"value": ""}], "content_encoded": "<p>Full body</p>"}) == "<p>Full body</p>"
