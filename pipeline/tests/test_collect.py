from datetime import UTC, datetime
from dataclasses import replace
from pathlib import Path
import socket

import pytest

from recsys_daily.collect import Candidate, FeedResponse, collect_candidates, stable_id
from recsys_daily.config import SourcesConfig, load_config
from recsys_daily.security import PublicUrlError, fetch_public_url, validate_public_url
from recsys_daily.schemas import SourceState, State
from recsys_daily.state import query_window


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "fixtures" / "sources"
NOW = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)


def _public_resolver(host: str, port: int, *_args: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _config():
    config = load_config(ROOT)
    sources = SourcesConfig(academic=config.sources.academic, blogs=[config.sources.blogs[0]])
    return config.model_copy(update={"sources": sources})


def test_query_window_uses_cold_start_and_incremental_offsets() -> None:
    cold = query_window(None, now=NOW)
    state = State(last_success_at=datetime(2026, 8, 9, 0, 0, tzinfo=UTC))
    incremental = query_window(state, now=NOW)

    assert cold.papers_since == datetime(2021, 8, 10, 0, 0, tzinfo=UTC)
    assert cold.blogs_since == datetime(2023, 8, 10, 0, 0, tzinfo=UTC)
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

    assert stable_id(replace(base, arxiv_id="arXiv:2608.01234v2")) == "arxiv-2608.01234v2"
    assert stable_id(replace(base, doi="https://doi.org/10.1234/Example.DOI")).startswith("doi-10.1234-")
    assert stable_id(base).startswith("url-")
    assert stable_id(replace(base, url=None)).startswith("title-")


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
            return FeedResponse(200, (FIXTURES / "arxiv.atom").read_bytes(), {"ETag": '"arxiv-v1"'})
        return FeedResponse(200, (FIXTURES / "blog.rss").read_bytes(), {"Last-Modified": "Sun, 09 Aug 2026 08:30:00 GMT"})

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
    assert stable_id(paper) == "arxiv-2608.01234v2"
    assert paper.authors == ("Ada Lovelace", "Grace Hopper")
    assert blog.url == "https://engineering.example.com/posts/feed-ranking"
    assert len(blog.excerpt) <= _config().settings.storage.max_blog_excerpt_chars
    assert result.source_states["arxiv"].etag == '"arxiv-v1"'


def test_optional_feed_failure_is_a_warning_but_required_failure_stops_collection() -> None:
    def fail_fetcher(_url: str, _headers: dict[str, str]) -> FeedResponse:
        raise RuntimeError("offline")

    with pytest.raises(RuntimeError, match="arxiv"):
        collect_candidates(_config(), now=NOW, fetcher=fail_fetcher, resolver=_public_resolver)

    def arxiv_only(url: str, _headers: dict[str, str]) -> FeedResponse:
        if "export.arxiv.org" in url:
            return FeedResponse(200, (FIXTURES / "arxiv.atom").read_bytes(), {})
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
