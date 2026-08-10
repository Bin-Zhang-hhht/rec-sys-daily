"""Configuration-driven candidate metadata analysis for Stage 1."""

from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .collect import Candidate, stable_id
from .config import AppConfig, TopicTaxonomy
from .prompts import metadata_messages
from .schemas import Stage1Metadata


@dataclass(frozen=True)
class MetadataResult:
    items: list[Stage1Metadata]
    llm_calls: int
    success_rate: float
    degraded_count: int


def metadata_json_schema(taxonomy: TopicTaxonomy) -> dict[str, Any]:
    """Return a strict response schema whose label enums come from topics.yaml."""
    relation_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "type": {"type": "string", "minLength": 1},
            "target_id": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string", "minLength": 1},
            "generated_by": {"type": "string", "minLength": 1},
        },
        "required": ["type", "target_id", "confidence", "evidence", "generated_by"],
    }

    def labels(category: str) -> dict[str, Any]:
        return {
            "type": "array",
            "items": {"type": "string", "enum": [entry.id for entry in getattr(taxonomy, category)]},
            "uniqueItems": True,
        }

    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "summary_zh": {"type": ["string", "null"]},
            "targets": labels("targets"),
            "scenarios": labels("scenarios"),
            "tasks": labels("tasks"),
            "methods": labels("methods"),
            "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
            "graph_relations": {"type": "array", "items": relation_schema},
            "degraded": {"type": "boolean"},
        },
        "required": ["id", "summary_zh", "targets", "scenarios", "tasks", "methods", "relevance_score", "graph_relations", "degraded"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": item_schema}},
        "required": ["items"],
    }


def _matches(text: str, terms: Sequence[str]) -> bool:
    normalized = text.casefold()
    return any(term.strip().casefold() in normalized for term in terms if term and term.strip())


def _fallback(candidate: Candidate, taxonomy: TopicTaxonomy) -> Stage1Metadata:
    text = " ".join((candidate.title, candidate.excerpt, *candidate.categories, *candidate.source_scenarios))

    def selected(category: str) -> list[str]:
        values = [entry.id for entry in getattr(taxonomy, category) if _matches(text, entry.terms)]
        if category == "scenarios":
            allowed = {entry.id for entry in getattr(taxonomy, category)}
            values = list(dict.fromkeys([item for item in candidate.source_scenarios if item in allowed] + values))
        return values

    excerpt = candidate.excerpt.strip()
    return Stage1Metadata(
        id=stable_id(candidate),
        summary_zh=excerpt[:4000] or None,
        targets=selected("targets"),
        scenarios=selected("scenarios"),
        tasks=selected("tasks"),
        methods=selected("methods"),
        relevance_score=max(0.0, min(1.0, candidate.metadata_score)),
        graph_relations=[],
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
    for start in range(0, len(candidates), batch_size):
        batch = list(candidates[start : start + batch_size])
        if not batch:
            continue
        calls += 1
        expected = {stable_id(candidate) for candidate in batch}
        try:
            response = complete_json(metadata_messages(batch), schema)
            raw_items = response.get("items") if isinstance(response, Mapping) else None
            if not isinstance(raw_items, list):
                raise ValueError("metadata response must contain an items list")
            parsed = [Stage1Metadata.model_validate(item) for item in raw_items]
            returned = {item.id for item in parsed}
            if returned != expected or len(parsed) != len(returned):
                raise ValueError("metadata response must contain exactly the requested candidates")
            if any(set(getattr(item, category)) - {entry.id for entry in getattr(config.topics, category)} for item in parsed for category in ("targets", "scenarios", "tasks", "methods")):
                raise ValueError("metadata response contains an unknown taxonomy id")
            for item in parsed:
                all_items[item.id] = item
            successes += 1
        except Exception:
            for candidate in batch:
                item = _fallback(candidate, config.topics)
                all_items[item.id] = item
                degraded += 1
    ordered = [all_items[stable_id(candidate)] for candidate in candidates if stable_id(candidate) in all_items]
    return MetadataResult(
        items=ordered,
        llm_calls=calls,
        success_rate=(successes / calls) if calls else 1.0,
        degraded_count=degraded,
    )
