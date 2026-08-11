"""Bounded, temporary content access helpers for deep reading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from collections.abc import Mapping
from typing import Callable
from urllib.parse import urlsplit

import fitz
import trafilatura

from .security import fetch_public_url
from .collect import Candidate, normalize_title, normalize_url, parse_blog_feed, stable_id


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


class DomainRateLimiter:
    """Serialize article fetches and pace requests independently per hostname."""

    def __init__(
        self,
        min_interval_seconds: float = 2.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be positive")
        self.min_interval_seconds = float(min_interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, url: str) -> None:
        try:
            hostname = urlsplit(url).hostname
        except ValueError as exc:
            raise ValueError("blog URL is malformed") from exc
        if not hostname:
            raise ValueError("blog URL has no hostname")
        hostname = hostname.casefold().rstrip(".")
        with self._lock:
            now = self._clock()
            last = self._last.get(hostname)
            if last is not None:
                wait = self.min_interval_seconds - (now - last)
                if wait > 0:
                    self._sleeper(wait)
                    now = self._clock()
            self._last[hostname] = now


def fetch_bytes(
    url: str,
    max_bytes: int,
    *,
    timeout: float = 45,
    max_attempts: int = 3,
    user_agent: str | None = None,
) -> bytes:
    response = fetch_public_url(url, timeout=timeout, max_attempts=max_attempts, user_agent=user_agent)
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
) -> str:
    return fetch_bytes(
        url,
        max_bytes,
        timeout=timeout,
        max_attempts=max_attempts,
        user_agent=user_agent,
    ).decode("utf-8", errors="replace")


def extract_article(html: str) -> str:
    extracted = trafilatura.extract(html, include_comments=False, include_tables=True, output_format="txt")
    return (extracted or "").strip()


def extract_pdf(path: Path, max_pages: int) -> tuple[str, list[PageText]]:
    pages: list[PageText] = []
    with fitz.open(path) as document:
        if document.page_count > max_pages:
            raise ValueError(f"PDF exceeds {max_pages} pages")
        for index, page in enumerate(document, start=1):
            pages.append(PageText(index, page.get_text("text").strip()))
    return "\n\n".join(page.text for page in pages if page.text), pages


def critical_pages(pages: list[PageText]) -> list[int]:
    """Select every page whose text/captions suggest a key visual or result."""
    keywords = ("figure", "table", "overview", "architecture", "main results", "ablation", "case study")
    selected: list[int] = []
    for page in pages:
        text = page.text.casefold()
        if any(keyword in text for keyword in keywords):
            selected.append(page.page)
    return selected


def render_pages(pdf_path: Path, pages: list[int], directory: Path) -> list[Path]:
    rendered: list[Path] = []
    with fitz.open(pdf_path) as document:
        for page_number in pages:
            if page_number < 1 or page_number > document.page_count:
                continue
            output = directory / f"page-{page_number}.png"
            document.load_page(page_number - 1).get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(output)
            rendered.append(output)
    return rendered


def fetch_article_html(
    candidate: object,
    max_bytes: int = 5 * 1024 * 1024,
    *,
    timeout: float = 45,
    max_attempts: int = 3,
    user_agent: str | None = None,
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
        max_requests_per_source: int = 1,
    ) -> None:
        if max_requests_per_source < 1:
            raise ValueError("max_requests_per_source must be positive")
        self._source_urls = dict(source_urls)
        self._fetch_feed = fetch_feed
        self._parse_feed = parse_feed
        self._max_requests_per_source = max_requests_per_source
        self._attempts: dict[str, int] = {}
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
        if self._attempts.get(source_id, 0) >= self._max_requests_per_source:
            return None
        self._attempts[source_id] = self._attempts.get(source_id, 0) + 1
        try:
            candidates = self._parse_feed(self._fetch_feed(source_id, self._source_urls[source_id]), source_id)
        except Exception:
            return None
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
    """Dependency-injection point for network, extraction, and rendering work."""

    fetch_text: Callable[[str, int], str] = fetch_text
    fetch_bytes: Callable[[str, int], bytes] = fetch_bytes
    extract_pdf: Callable[[Path, int], tuple[str, list[PageText]]] = extract_pdf
    critical_pages: Callable[[list[PageText]], list[int]] = critical_pages
    render_pages: Callable[[Path, list[int], Path], list[Path]] = render_pages
    extract_article: Callable[[str], str] = extract_article
    feed_content: Callable[[object], str | None] = lambda candidate: getattr(candidate, "feed_content", None)
    fetch_article_html: Callable[[object], str] | None = fetch_article_html


def arxiv_urls(arxiv_id: str) -> tuple[str, str]:
    identifier = arxiv_id.removeprefix("arXiv:").removesuffix(".pdf").removesuffix(".html")
    return f"https://arxiv.org/html/{identifier}", f"https://arxiv.org/pdf/{identifier}.pdf"
