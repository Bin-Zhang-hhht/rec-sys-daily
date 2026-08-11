"""Deterministic metadata prefiltering before model calls."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import re
from collections.abc import Iterable

from .collect import Candidate, normalize_title, stable_id
from .config import AppConfig
from .schemas import State


def _term_score(terms: Iterable[str], text: str) -> float:
    terms = [term.casefold().strip() for term in terms if term and term.strip()]
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I))
    return min(1.0, matched / len(terms))


def _history_ids(history: State | Iterable[str] | None) -> set[str]:
    if history is None:
        return set()
    if isinstance(history, State):
        return set(history.recommended_item_ids)
    return {str(value) for value in history}


def _score(candidate: Candidate, config: AppConfig, history_ids: set[str], now: datetime) -> float:
    text = normalize_title(f"{candidate.title} {candidate.excerpt} {' '.join(candidate.categories)}")
    topic_terms = [term for category in (config.topics.targets, config.topics.tasks, config.topics.methods) for entry in category for term in entry.terms]
    scenario_terms = [term for entry in config.topics.scenarios for term in entry.terms]
    topic_relevance = _term_score(topic_terms, text)
    scenario_relevance = min(1.0, max(_term_score(scenario_terms, text), _term_score(candidate.source_scenarios, text)))
    max_weight = max((source.weight for source in [*config.sources.academic, *config.sources.blogs]), default=1.0)
    source_quality = max(0.0, min(1.0, candidate.source_weight / max_weight))
    novelty = 0.0 if stable_id(candidate) in history_ids else 1.0
    practical_value = min(1.0, (topic_relevance + scenario_relevance + (1.0 if candidate.kind == "blog" else 0.0)) / 2.0)
    age_days = max(0.0, (now - candidate.published_at).total_seconds() / 86_400)
    recency = max(0.0, min(1.0, 1.0 - age_days / (1_095 if candidate.kind == "blog" else 1_825)))
    weights = config.settings.metadata_weights
    return (
        weights.topic_relevance * topic_relevance
        + weights.scenario_relevance * scenario_relevance
        + weights.source_quality * source_quality
        + weights.novelty * novelty
        + weights.practical_value * practical_value
        + weights.recency * recency
    )


def deterministic_sort_key(candidate: Candidate) -> tuple[float, float, str, str]:
    return (-candidate.metadata_score, -candidate.published_at.timestamp(), candidate.source_id, stable_id(candidate))


def prefilter(
    candidates: Iterable[Candidate],
    config: AppConfig,
    history: State | Iterable[str] | None = None,
    *,
    now: datetime | None = None,
) -> list[Candidate]:
    """Score, cap, and deterministically order candidates by source kind."""
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() != datetime.now(UTC).utcoffset():
        raise ValueError("now must be timezone-aware UTC")
    ids = _history_ids(history)
    unique: dict[str, Candidate] = {}
    for candidate in candidates:
        if stable_id(candidate) in ids:
            continue
        score = _score(candidate, config, ids, current)
        scored = replace(candidate, metadata_score=score)
        key = stable_id(scored)
        previous = unique.get(key)
        if previous is None or deterministic_sort_key(scored) < deterministic_sort_key(previous):
            unique[key] = scored
    papers = sorted((item for item in unique.values() if item.kind == "paper"), key=deterministic_sort_key)[: config.settings.limits.max_papers_per_run]
    blogs = sorted((item for item in unique.values() if item.kind == "blog"), key=deterministic_sort_key)[: config.settings.limits.max_blogs_per_run]
    return sorted([*papers, *blogs], key=deterministic_sort_key)
