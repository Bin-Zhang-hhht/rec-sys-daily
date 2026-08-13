"""Bounded, temporary content access helpers for deep reading."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable

import trafilatura

from .security import fetch_public_url
from .rate_limit import DomainRateLimiter
from .collect import Candidate, normalize_title, normalize_url, parse_blog_feed, stable_id


def fetch_bytes(
    url: str,
    max_bytes: int,
    *,
    timeout: float = 45,
    max_attempts: int = 3,
    user_agent: str | None = None,
    attempt_limiter: Callable[[], None] | None = None,
    backoff_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
) -> bytes:
    response = fetch_public_url(
        url,
        timeout=timeout,
        max_attempts=max_attempts,
        user_agent=user_agent,
        attempt_limiter=attempt_limiter,
        backoff_seconds=backoff_seconds,
        max_delay_seconds=max_delay_seconds,
    )
    declared_size = response.headers.get("Content-Length")
    if declared_size:
        try:
            declared_bytes = int(declared_size)
        except (TypeError, ValueError):
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > max_bytes:
            raise ValueError(f"content exceeds {max_bytes} bytes")
    content = bytes(response.content)
    if len(content) > max_bytes:
        raise ValueError(f"content exceeds {max_bytes} bytes")
    return content


def fetch_text(
    url: str,
    max_bytes: int,
    *,
    timeout: float = 45,
    max_attempts: int = 3,
    user_agent: str | None = None,
    attempt_limiter: Callable[[], None] | None = None,
    backoff_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
) -> str:
    return fetch_bytes(
        url,
        max_bytes,
        timeout=timeout,
        max_attempts=max_attempts,
        user_agent=user_agent,
        attempt_limiter=attempt_limiter,
        backoff_seconds=backoff_seconds,
        max_delay_seconds=max_delay_seconds,
    ).decode("utf-8", errors="replace")


def extract_article(html: str) -> str:
    extracted = trafilatura.extract(html, include_comments=False, include_tables=True, output_format="txt")
    return (extracted or "").strip()


def fetch_article_html(
    candidate: object,
    max_bytes: int = 5 * 1024 * 1024,
    *,
    timeout: float = 45,
    max_attempts: int = 3,
    user_agent: str | None = None,
    attempt_limiter: Callable[[], None] | None = None,
    backoff_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
) -> str:
    url = getattr(candidate, "url", None)
    if not url:
        raise ValueError("blog candidate has no public URL")
    return fetch_text(
        str(url),
        max_bytes,
        timeout=timeout,
        max_attempts=max_attempts,
        user_agent=user_agent,
        attempt_limiter=attempt_limiter,
        backoff_seconds=backoff_seconds,
        max_delay_seconds=max_delay_seconds,
    )


def parse_source_feed(payload: bytes | str, source_id: str) -> list[Candidate]:
    return parse_blog_feed(payload, source_id=source_id)


class BlogFeedCache:
    """Process-local second-feed cache; source payloads never reach disk."""

    def __init__(
        self,
        source_urls: Mapping[str, str],
        fetch_feed: Callable[[str, str], bytes | str],
        parse_feed: Callable[[bytes | str, str], list[Candidate]] = parse_source_feed,
    ) -> None:
        self._source_urls = dict(source_urls)
        self._fetch_feed = fetch_feed
        self._parse_feed = parse_feed
        self._loaded_sources: set[str] = set()
        self._content_by_id: dict[str, str] = {}
        self._content_by_url: dict[str, str] = {}
        self._content_by_title: dict[str, str] = {}

    def get(self, candidate: Candidate) -> str | None:
        source_id = candidate.source_id
        if source_id not in self._source_urls:
            return None
        keys = (stable_id(candidate), normalize_url(candidate.url), normalize_title(candidate.title))
        for key, values in zip(("id", "url", "title"), keys):
            if not values:
                continue
            store = {"id": self._content_by_id, "url": self._content_by_url, "title": self._content_by_title}[key]
            if values in store:
                return store[values]
        if source_id in self._loaded_sources:
            return None
        try:
            candidates = self._parse_feed(self._fetch_feed(source_id, self._source_urls[source_id]), source_id)
        except Exception:
            return None
        self._loaded_sources.add(source_id)
        for parsed in candidates:
            body = (parsed.feed_content or "").strip()
            if not body:
                continue
            self._content_by_id[stable_id(parsed)] = body
            if parsed.url:
                normalized_url = normalize_url(parsed.url)
                if normalized_url:
                    self._content_by_url[normalized_url] = body
            self._content_by_title[normalize_title(parsed.title)] = body
        return self.get(candidate)


@dataclass
class ContentServices:
    """Dependency-injection point for bounded network and article extraction work."""

    fetch_bytes: Callable[[str, int], bytes] = fetch_bytes
    extract_article: Callable[[str], str] = extract_article
    feed_content: Callable[[object], str | None] = lambda candidate: getattr(candidate, "feed_content", None)
    fetch_article_html: Callable[[object], str] | None = fetch_article_html


def arxiv_pdf_url(arxiv_id: str) -> str:
    identifier = arxiv_id.removeprefix("arXiv:").removesuffix(".pdf").removesuffix(".html")
    return f"https://arxiv.org/pdf/{identifier}.pdf"
