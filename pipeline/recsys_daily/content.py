"""Bounded, temporary content access helpers for deep reading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz
import trafilatura

from .security import fetch_public_url


@dataclass(frozen=True)
class PageText:
    page: int
    text: str


def fetch_bytes(url: str, max_bytes: int, *, timeout: float = 45) -> bytes:
    response = fetch_public_url(url, timeout=timeout)
    content = bytes(response.content)
    if len(content) > max_bytes:
        raise ValueError(f"content exceeds {max_bytes} bytes")
    return content


def fetch_text(url: str, max_bytes: int, *, timeout: float = 45) -> str:
    return fetch_bytes(url, max_bytes, timeout=timeout).decode("utf-8", errors="replace")


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


def fetch_article_html(candidate: object, max_bytes: int = 5 * 1024 * 1024) -> str:
    url = getattr(candidate, "url", None)
    if not url:
        raise ValueError("blog candidate has no public URL")
    return fetch_text(str(url), max_bytes)


@dataclass
class ContentServices:
    """Dependency-injection point for network, extraction, and rendering work."""

    fetch_text: Callable[[str, int], str] = fetch_text
    fetch_bytes: Callable[[str, int], bytes] = fetch_bytes
    extract_pdf: Callable[[Path, int], tuple[str, list[PageText]]] = extract_pdf
    critical_pages: Callable[[list[PageText]], list[int]] = critical_pages
    render_pages: Callable[[Path, list[int], Path], list[Path]] = render_pages
    extract_article: Callable[[str], str] = extract_article
    feed_content: Callable[[object], str | None] = lambda _candidate: None
    fetch_article_html: Callable[[object], str] | None = fetch_article_html


def arxiv_urls(arxiv_id: str) -> tuple[str, str]:
    identifier = arxiv_id.removeprefix("arXiv:").removesuffix(".pdf").removesuffix(".html")
    return f"https://arxiv.org/html/{identifier}", f"https://arxiv.org/pdf/{identifier}.pdf"
