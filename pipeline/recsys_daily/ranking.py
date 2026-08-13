"""Deterministic final ranking for canonical content items."""

from __future__ import annotations

from collections.abc import Iterable

from .config import FinalScoreWeights
from .schemas import BlogItem, ContentItem, PaperItem


def _bounded_count(values: Iterable[object], denominator: int = 3) -> float:
    return min(1.0, sum(bool(value) for value in values) / denominator)


def _evidence_quality(item: PaperItem | BlogItem) -> float:
    explicit = getattr(item.deep_reading, "evidence_quality", None)
    if explicit is not None:
        return explicit
    references = item.deep_reading.evidence_refs
    return min(1.0, len(references) / 3)


def _business_transferability(item: PaperItem | BlogItem) -> float:
    explicit = getattr(item.deep_reading, "business_transferability", None)
    if explicit is not None:
        return explicit
    return _bounded_count(item.deep_reading.business_implications_zh)


def _technical_depth(item: PaperItem | BlogItem) -> float:
    explicit = getattr(item.deep_reading, "technical_depth", None)
    if explicit is not None:
        return explicit
    reading = item.deep_reading
    if item.kind == "paper":
        experiments = reading.experiments
        return _bounded_count(
            [reading.problem_zh, reading.method_zh, *reading.contributions_zh, *experiments.datasets, *experiments.metrics],
            denominator=6,
        )
    return _bounded_count(
        [reading.system_context_zh, reading.architecture_zh, reading.implementation_zh, *reading.lessons_zh],
        denominator=4,
    )


def _final_score(item: PaperItem | BlogItem, weights: FinalScoreWeights) -> float:
    score = (
        weights.metadata_score * item.relevance_score
        + weights.evidence_quality * _evidence_quality(item)
        + weights.business_transferability * _business_transferability(item)
        + weights.technical_depth * _technical_depth(item)
    )
    item.final_score = max(0.0, min(1.0, score))
    return item.final_score


def rank_items(
    items: Iterable[ContentItem],
    kind: str,
    limit: int = 8,
    *,
    final_weights: FinalScoreWeights | None = None,
    minimum_final_score: float = 0.0,
) -> list[PaperItem | BlogItem]:
    """Return the best items of one kind with stable tie-breaking."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if not 0 <= minimum_final_score <= 1:
        raise ValueError("minimum_final_score must be between 0 and 1")
    selected = [item for item in items if item.kind == kind]
    if final_weights is None:
        for item in selected:
            item.final_score = item.relevance_score
        selected.sort(key=lambda item: (-item.relevance_score, -item.published_at.timestamp(), item.source, item.id))
    else:
        selected.sort(
            key=lambda item: (
                -_final_score(item, final_weights),
                -item.published_at.timestamp(),
                item.source,
                item.id,
            )
        )
    return [item for item in selected if item.final_score >= minimum_final_score][:limit]
