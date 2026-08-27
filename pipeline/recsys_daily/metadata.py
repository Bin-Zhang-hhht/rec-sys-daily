"""Configuration-driven candidate metadata analysis for Stage 1."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .collect import Candidate, normalize_title, stable_id
from .config import AppConfig, TopicTaxonomy
from .prompts import metadata_messages
from .schemas import Stage1Metadata


_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def has_cjk(value: str | None) -> bool:
    return bool(value and _CJK_PATTERN.search(value))


@dataclass(frozen=True)
class MetadataResult:
    items: list[Stage1Metadata]
    llm_calls: int
    success_rate: float
    degraded_count: int
    label_rejections: int = 0


def metadata_json_schema(taxonomy: TopicTaxonomy) -> dict[str, Any]:
    """Return a strict response schema whose label enums come from topics.yaml."""
    def labels(category: str) -> dict[str, Any]:
        return {
            "type": "array",
            "items": {"type": "string", "enum": [entry.id for entry in getattr(taxonomy, category)]},
            "minItems": 0,
            "uniqueItems": True,
        }

    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "summary_zh": {"type": "string", "minLength": 1},
            "targets": labels("targets"),
            "scenarios": labels("scenarios"),
            "tasks": labels("tasks"),
            "methods": labels("methods"),
            "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["id", "summary_zh", "targets", "scenarios", "tasks", "methods", "relevance_score"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": item_schema}},
        "required": ["items"],
    }


def _matches(text: str, terms: Sequence[str]) -> bool:
    normalized = normalize_title(text)
    return any(
        re.search(r"(?<!\w)" + re.escape(term.strip().casefold()) + r"(?!\w)", normalized)
        for term in terms
        if term and term.strip()
    )


def _candidate_text(candidate: Candidate) -> str:
    return " ".join((candidate.title, candidate.excerpt, *candidate.categories))


def _ground_labels(item: Stage1Metadata, candidate: Candidate, taxonomy: TopicTaxonomy) -> tuple[Stage1Metadata, int]:
    text = _candidate_text(candidate)
    updates: dict[str, list[str]] = {}
    rejected = 0
    for category in ("targets", "scenarios", "tasks", "methods"):
        supported: list[str] = []
        entries = {entry.id: entry for entry in getattr(taxonomy, category)}
        for label_id in getattr(item, category):
            entry = entries.get(label_id)
            if entry is not None and _matches(text, entry.terms):
                supported.append(label_id)
            else:
                rejected += 1
        updates[category] = supported
    return item.model_copy(update=updates), rejected


def _fallback(candidate: Candidate, taxonomy: TopicTaxonomy, excerpt_limit: int) -> Stage1Metadata:
    # Source-wide scenarios describe the feed, not evidence about this item.
    text = " ".join((candidate.title, candidate.excerpt, *candidate.categories))

    def selected(category: str) -> list[str]:
        values = [entry.id for entry in getattr(taxonomy, category) if _matches(text, entry.terms)]
        return values

    excerpt = candidate.excerpt.strip()[:excerpt_limit]
    return Stage1Metadata(
        id=stable_id(candidate),
        summary_zh=excerpt if has_cjk(excerpt) else None,
        targets=selected("targets"),
        scenarios=selected("scenarios"),
        tasks=selected("tasks"),
        methods=selected("methods"),
        relevance_score=max(0.0, min(1.0, candidate.metadata_score)),
        degraded=True,
    )


def analyze_metadata(
    candidates: Sequence[Candidate],
    config: AppConfig,
    complete_json: Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]],
) -> MetadataResult:
    """Analyze candidates in configured batches and degrade failed batches explicitly."""
    batch_size = config.models.text.batch_size
    schema = metadata_json_schema(config.topics)
    all_items: dict[str, Stage1Metadata] = {}
    calls = 0
    successes = 0
    degraded = 0
    label_rejections = 0
    for start in range(0, len(candidates), batch_size):
        batch = list(candidates[start : start + batch_size])
        if not batch:
            continue
        calls += 1
        expected = {stable_id(candidate) for candidate in batch}
        try:
            response = complete_json(metadata_messages(batch, config.topics), schema)
            raw_items = response.get("items") if isinstance(response, Mapping) else None
            if not isinstance(raw_items, list):
                raise ValueError("metadata response must contain an items list")
            parsed = [Stage1Metadata.model_validate({**item, "degraded": False}) for item in raw_items]
            returned = {item.id for item in parsed}
            if returned != expected or len(parsed) != len(returned):
                raise ValueError("metadata response must contain exactly the requested candidates")
            if any(set(getattr(item, category)) - {entry.id for entry in getattr(config.topics, category)} for item in parsed for category in ("targets", "scenarios", "tasks", "methods")):
                raise ValueError("metadata response contains an unknown taxonomy id")
            if any(not has_cjk(item.summary_zh) for item in parsed):
                raise ValueError("metadata response summary_zh must contain CJK text")
            candidates_by_id = {stable_id(candidate): candidate for candidate in batch}
            for item in parsed:
                grounded, rejected = _ground_labels(item, candidates_by_id[item.id], config.topics)
                all_items[item.id] = grounded
                label_rejections += rejected
            successes += 1
        except Exception:
            for candidate in batch:
                item = _fallback(candidate, config.topics, config.settings.storage.max_blog_excerpt_chars)
                all_items[item.id] = item
                degraded += 1
    ordered = [all_items[stable_id(candidate)] for candidate in candidates if stable_id(candidate) in all_items]
    return MetadataResult(
        items=ordered,
        llm_calls=calls,
        success_rate=(successes / calls) if calls else 1.0,
        degraded_count=degraded,
        label_rejections=label_rejections,
    )
