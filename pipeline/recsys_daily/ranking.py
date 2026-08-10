"""Deterministic final ranking for canonical content items."""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import BlogItem, ContentItem, PaperItem


def rank_items(items: Iterable[ContentItem], kind: str, limit: int = 8) -> list[PaperItem | BlogItem]:
    """Return the best items of one kind with stable tie-breaking."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    selected = [item for item in items if item.kind == kind]
    selected.sort(key=lambda item: (-item.relevance_score, -item.published_at.timestamp(), item.id))
    return selected[:limit]
