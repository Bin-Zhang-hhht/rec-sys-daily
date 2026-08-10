"""Feed collection and bounded candidate normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import html
import re
import socket
from collections.abc import Callable, Mapping
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import unicodedata

import feedparser
from dateutil import parser as date_parser

from .config import AppConfig, AcademicSource, BlogSource
from .schemas import SourceState, State
from .security import Resolver, PublicUrlError, fetch_public_url, validate_public_url
from .state import QueryWindow, query_window


@dataclass
class Candidate:
    kind: Literal["paper", "blog"]
    source_id: str
    title: str
    url: str | None
    published_at: datetime
    authors: tuple[str, ...] = ()
    excerpt: str = ""
    # Feed content is retained only while candidates stay in the process.  It
    # is deliberately omitted from cross-stage artifacts because raw source
    # text must not be persisted.
    feed_content: str | None = field(default=None, repr=False, compare=False)
    source_entry_id: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    categories: tuple[str, ...] = ()
    source_weight: float = 1.0
    source_scenarios: tuple[str, ...] = ()
    metadata_score: float = field(default=0.0, compare=False)


@dataclass(frozen=True)
class FeedResponse:
    status_code: int
    content: bytes | str
    headers: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None


@dataclass
class CollectionResult:
    window: QueryWindow
    candidates: list[Candidate]
    warnings: list[str] = field(default_factory=list)
    source_states: dict[str, SourceState] = field(default_factory=dict)


class CollectionError(RuntimeError):
    pass


def normalize_title(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r"\s+", " ", value).strip().casefold()


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    normalized = html.unescape(str(value)).strip().strip("<>").lower()
    normalized = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", normalized)
    normalized = re.sub(r"^doi:\s*", "", normalized)
    normalized = normalized.rstrip(".,;)")
    return normalized if normalized.startswith("10.") and "/" in normalized else None


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.lower().rstrip(".")
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = host
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]"
    if parts.username or parts.password:
        return None
    if port and port != (443 if parts.scheme.lower() == "https" else 80):
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    )
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query), ""))


def _slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9._-]+", "-", value.casefold())).strip("-")


def _candidate_arxiv_id(candidate: Candidate) -> str | None:
    value = candidate.arxiv_id or ""
    if not value and candidate.source_entry_id:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#]+)", candidate.source_entry_id, re.I)
        value = match.group(1) if match else ""
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I).strip()
    value = re.sub(r"\.(?:pdf|html)$", "", value, flags=re.I)
    value = re.sub(r"v\d+$", "", value, flags=re.I)
    return value.replace("/", "-") or None


def stable_id(candidate: Candidate) -> str:
    """Return the stable ID using the documented identity precedence."""
    arxiv_id = _candidate_arxiv_id(candidate)
    if arxiv_id:
        return f"arxiv-{_slug(arxiv_id)}"
    doi = normalize_doi(candidate.doi)
    if doi:
        return f"doi-{_slug(doi)}"
    url = normalize_url(candidate.url)
    if url:
        return f"url-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}"
    title = normalize_title(candidate.title)
    return f"title-{hashlib.sha256(title.encode('utf-8')).hexdigest()[:24]}"


def _entry_value(entry: Any, name: str, default: Any = None) -> Any:
    value = entry.get(name, default)
    return value if value not in (None, "") else default


def _published_at(entry: Any) -> datetime | None:
    for name in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = _entry_value(entry, name)
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=UTC)
            except (TypeError, ValueError):
                pass
    for name in ("published", "updated", "created", "date"):
        value = _entry_value(entry, name)
        if value:
            try:
                result = date_parser.parse(str(value))
            except (TypeError, ValueError, OverflowError):
                continue
            return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)
    return None


_TAG_RE = re.compile(r"<[^>]*>")


def _bounded_text(value: Any, limit: int = 4_000) -> str:
    if isinstance(value, list):
        value = " ".join(str(item.get("value", "")) if isinstance(item, dict) else str(item) for item in value)
    text = html.unescape(_TAG_RE.sub(" ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _entry_excerpt(entry: Any, limit: int = 4_000) -> str:
    values: list[str] = []
    for name in ("summary", "description", "content"):
        value = _entry_value(entry, name)
        if value:
            values.append(_bounded_text(value, limit))
    return _bounded_text(" ".join(values), limit)


def _entry_feed_content(entry: Any) -> str | None:
    """Return RSS/Atom full content without copying it into the excerpt."""
    value = _entry_value(entry, "content")
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and part.get("value"):
                return str(part["value"]).strip() or None
    elif isinstance(value, dict) and value.get("value"):
        return str(value["value"]).strip() or None
    elif value:
        return str(value).strip() or None
    for name in ("content:encoded", "content_encoded"):
        value = _entry_value(entry, name)
        if value:
            return str(value).strip() or None
    return None


def _authors(entry: Any) -> tuple[str, ...]:
    values: list[str] = []
    for author in _entry_value(entry, "authors", []) or []:
        name = author.get("name") if isinstance(author, dict) else str(author)
        if name and str(name).strip():
            values.append(str(name).strip())
    if not values:
        author = _entry_value(entry, "author")
        if author:
            match = re.search(r"\(([^()]+)\)", str(author))
            values.append((match.group(1) if match else str(author)).strip())
    return tuple(dict.fromkeys(values))


def _entry_url(entry: Any) -> str | None:
    links = _entry_value(entry, "links", []) or []
    for link in links:
        if isinstance(link, dict) and link.get("rel") in {"alternate", "canonical"} and link.get("href"):
            return str(link["href"])
    return _entry_value(entry, "link")


def _categories(entry: Any) -> tuple[str, ...]:
    result: list[str] = []
    for tag in _entry_value(entry, "tags", []) or []:
        term = tag.get("term") if isinstance(tag, dict) else None
        if term:
            result.append(str(term))
    primary = _entry_value(entry, "arxiv_primary_category")
    if isinstance(primary, dict) and primary.get("term"):
        result.append(str(primary["term"]))
    return tuple(dict.fromkeys(result))


def parse_arxiv_atom(payload: bytes | str, *, source_id: str = "arxiv", source_weight: float = 1.0) -> list[Candidate]:
    parsed = feedparser.parse(payload)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError("invalid arXiv Atom feed")
    candidates: list[Candidate] = []
    for entry in parsed.entries:
        published_at = _published_at(entry)
        title = _bounded_text(_entry_value(entry, "title"), 1_000)
        url = normalize_url(_entry_url(entry))
        if not title or not published_at or not url:
            continue
        entry_id = str(_entry_value(entry, "id", "")) or None
        arxiv_id = _candidate_arxiv_id(Candidate("paper", source_id, title, url, published_at, source_entry_id=entry_id))
        doi = normalize_doi(_entry_value(entry, "arxiv_doi") or _entry_value(entry, "doi"))
        candidates.append(Candidate(
            kind="paper", source_id=source_id, title=title, url=url, published_at=published_at,
            authors=_authors(entry), excerpt=_entry_excerpt(entry), source_entry_id=entry_id,
            arxiv_id=arxiv_id, doi=doi, categories=_categories(entry), source_weight=source_weight,
        ))
    return candidates


def parse_blog_feed(payload: bytes | str, *, source_id: str, source_weight: float = 1.0, scenarios: tuple[str, ...] = ()) -> list[Candidate]:
    parsed = feedparser.parse(payload)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError("invalid RSS/Atom feed")
    candidates: list[Candidate] = []
    for entry in parsed.entries:
        published_at = _published_at(entry)
        title = _bounded_text(_entry_value(entry, "title"), 1_000)
        url = normalize_url(_entry_url(entry))
        if not title or not published_at or not url:
            continue
        entry_id = str(_entry_value(entry, "id") or _entry_value(entry, "guid") or "") or None
        doi = normalize_doi(_entry_value(entry, "doi"))
        candidates.append(Candidate(
            kind="blog", source_id=source_id, title=title, url=url, published_at=published_at,
            authors=_authors(entry), excerpt=_entry_excerpt(entry), source_entry_id=entry_id,
            feed_content=_entry_feed_content(entry), doi=doi, source_weight=source_weight, source_scenarios=scenarios,
        ))
    return candidates


parse_arxiv_feed = parse_arxiv_atom


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lower:
            return str(value)
    return None


def _arxiv_url(config: AppConfig, window: QueryWindow) -> str:
    terms = [term for category in (config.topics.targets, config.topics.scenarios, config.topics.tasks, config.topics.methods) for entry in category for term in entry.terms]
    term_query = " OR ".join(f'all:"{term}"' for term in dict.fromkeys(terms)) or 'all:"recommendation"'
    submitted_window = (
        f"submittedDate:[{window.papers_since:%Y%m%d%H%M} TO "
        f"{window.until:%Y%m%d%H%M}]"
    )
    query = f"({term_query}) AND {submitted_window}"
    params = {
        "search_query": query,
        "start": 0,
        "max_results": config.settings.limits.max_papers_per_run,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    return "https://export.arxiv.org/api/query?" + urlencode(params)


def _default_fetcher(url: str, headers: Mapping[str, str], *, resolver: Resolver | None, timeout: float) -> FeedResponse:
    response = fetch_public_url(url, headers=headers, timeout=timeout, resolver=resolver)
    return FeedResponse(response.status_code, response.content, response.headers, response.url)


def _deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    selected: dict[str, Candidate] = {}
    for candidate in sorted(candidates, key=lambda item: (stable_id(item), -item.source_weight, -item.published_at.timestamp(), item.source_id)):
        selected.setdefault(stable_id(candidate), candidate)
    return list(selected.values())


def _validated_state(value: State | dict[str, Any] | None) -> State | None:
    if isinstance(value, State):
        return value
    if isinstance(value, dict):
        try:
            return State.model_validate(value)
        except ValueError:
            return None
    return None


def collect_candidates(
    config: AppConfig,
    state: State | dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
    fetcher: Callable[[str, Mapping[str, str]], FeedResponse] | None = None,
    resolver: Resolver | None = None,
) -> CollectionResult:
    """Fetch each configured source once and return bounded, normalized candidates."""
    window = query_window(state, now=now)
    state_value = _validated_state(state)
    current = window.until
    fetch = fetcher or (lambda url, headers: _default_fetcher(url, headers, resolver=resolver, timeout=config.settings.limits.request_timeout_seconds))
    all_candidates: list[Candidate] = []
    warnings: list[str] = []
    source_states: dict[str, SourceState] = {}
    configured: list[AcademicSource | BlogSource] = [source for source in [*config.sources.academic, *config.sources.blogs] if source.enabled]
    for source in configured:
        source_state = state_value.sources.get(source.id) if state_value else None
        headers: dict[str, str] = {}
        if source_state and source_state.etag:
            headers["If-None-Match"] = source_state.etag
        if source_state and source_state.last_modified:
            headers["If-Modified-Since"] = source_state.last_modified
        url = _arxiv_url(config, window) if source.kind == "arxiv" else source.url
        try:
            validate_public_url(url, resolver=resolver)
            response = fetch(url, headers)
            if response.status_code == 304:
                source_states[source.id] = SourceState(
                    cursor=source_state.cursor if source_state else None,
                    etag=_header(response.headers, "etag") or (source_state.etag if source_state else None),
                    last_modified=_header(response.headers, "last-modified") or (source_state.last_modified if source_state else None),
                    last_success_at=current,
                )
                continue
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"HTTP {response.status_code}")
            parsed_candidates = parse_arxiv_atom(response.content, source_id=source.id, source_weight=source.weight) if source.kind == "arxiv" else parse_blog_feed(response.content, source_id=source.id, source_weight=source.weight, scenarios=tuple(source.scenarios))
            lower_bound = window.papers_since if source.kind == "arxiv" else window.blogs_since
            for candidate in parsed_candidates:
                if not lower_bound <= candidate.published_at <= current:
                    continue
                validate_public_url(candidate.url or "", resolver=resolver)
                all_candidates.append(candidate)
            source_states[source.id] = SourceState(
                cursor=source_state.cursor if source_state else None,
                etag=_header(response.headers, "etag") or (source_state.etag if source_state else None),
                last_modified=_header(response.headers, "last-modified") or (source_state.last_modified if source_state else None),
                last_success_at=current,
            )
        except Exception as exc:
            if source.required:
                raise CollectionError(f"{source.id}: {exc}") from exc
            warnings.append(f"{source.id}: {exc}")
    return CollectionResult(window=window, candidates=_deduplicate(all_candidates), warnings=warnings, source_states=source_states)
