"""Validate stage outputs and assemble an atomic publish bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .artifacts import read_json, read_jsonl, write_json
from .config import AppConfig
from .ranking import rank_items
from .schemas import (
    BlogItem,
    ContentItem,
    Digest,
    DigestEntry,
    Manifest,
    LLMMetadata,
    PaperItem,
    RunReport,
    SourceState,
    State,
)


@dataclass(frozen=True)
class StageInputs:
    stage1: Path
    paper: Path
    blog: Path


@dataclass(frozen=True)
class PublishBundle:
    path: Path
    manifest: Manifest


def _manifest(path: Path) -> Manifest:
    return Manifest.model_validate(read_json(path / "manifest.json"))


def _stage_values(path: Path, kind: str) -> list[dict[str, Any]]:
    candidates = [
        path / "items.jsonl",
        path / f"{kind}s.jsonl",
        path / f"{kind}.jsonl",
        path / f"{kind}-deep-readings.json",
    ]
    source = next((candidate for candidate in candidates if candidate.exists()), None)
    if source is None:
        raise ValueError(f"missing {kind} deep-reading artifact in {path}")
    if source.suffix == ".jsonl":
        return read_jsonl(source)
    document = read_json(source)
    values = document.get("items", document)
    if not isinstance(values, list) or not all(isinstance(value, dict) for value in values):
        raise ValueError(f"deep-reading artifact must contain item objects: {source}")
    return values


def _candidate_metadata(stage1: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for name in ("items.jsonl", "papers.jsonl", "blogs.jsonl", "paper-candidates.json", "blog-candidates.json", "candidates.json"):
        source = stage1 / name
        if not source.exists():
            continue
        document = None if source.suffix == ".jsonl" else read_json(source)
        values = read_jsonl(source) if source.suffix == ".jsonl" else document.get("items", document.get("candidates", []))
        if isinstance(values, list):
            for value in values:
                if not isinstance(value, dict):
                    raise ValueError(f"candidate artifact contains a non-object value: {source}")
                if not value.get("id"):
                    raise ValueError(f"candidate id is required: {source}")
                candidate_id = str(value["id"])
                if candidate_id in metadata:
                    raise ValueError(f"duplicate candidate id in stage-1: {candidate_id}")
                metadata[candidate_id] = value
    return metadata


def _source_states(stage1: Path) -> dict[str, SourceState]:
    for name in ("source-states.json", "source_states.json"):
        source = stage1 / name
        if source.exists():
            document = read_json(source)
            result: dict[str, SourceState] = {}
            for source_id, value in document.items():
                if not isinstance(value, dict):
                    raise ValueError(f"source state must be an object: {source_id}")
                result[str(source_id)] = SourceState.model_validate(value)
            return result
    return {}


def _items(path: Path, kind: str, taxonomy: Any, metadata: dict[str, dict[str, Any]]) -> list[ContentItem]:
    parsed: list[ContentItem] = []
    for value in _stage_values(path, kind):
        item_id = str(value.get("id", ""))
        if not item_id:
            raise ValueError(f"candidate id is required in {path}")
        if item_id not in metadata:
            raise ValueError(f"deep-reading candidate id is not present in stage-1: {item_id}")
        if "title" not in value or "published_at" not in value:
            base = dict(metadata[item_id])
            base.update(value)
            value = base
        if "source" not in value and value.get("source_id"):
            value["source"] = value["source_id"]
        value.setdefault("summary_zh", value.get("excerpt") or value.get("title") or "")
        value.setdefault("relevance_score", value.get("metadata_score", 0.0))
        value.setdefault("authors", [])
        value.setdefault("targets", ["content"])
        value.setdefault("scenarios", ["text_feed"])
        value.setdefault("tasks", ["ranking"])
        value.setdefault("methods", ["two_tower"])
        for key in ("source_id", "source_entry_id", "arxiv_id", "doi", "categories", "source_weight", "source_scenarios", "metadata_score"):
            value.pop(key, None)
        if kind == "paper":
            value.pop("excerpt", None)
        item_type = PaperItem if kind == "paper" else BlogItem
        parsed.append(item_type.model_validate(value, context={"taxonomy": taxonomy}))
    return parsed


def _structured_success_rate(
    metadata: dict[str, dict[str, Any]],
    items: list[ContentItem],
    kind: str,
    max_deep_reads: int,
) -> float:
    expected = [item_id for item_id, value in metadata.items() if value.get("kind") == kind][:max_deep_reads]
    if not expected:
        return 1.0
    successful = {item.id for item in items if item.kind == kind}
    return len(set(expected) & successful) / len(expected)


def _load_previous_state(value: State | dict[str, Any] | None) -> State | None:
    if value is None:
        return None
    return value if isinstance(value, State) else State.model_validate(value)


def _write_item(root: Path, item: PaperItem | BlogItem, max_item_bytes: int) -> None:
    encoded = (json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > max_item_bytes:
        raise ValueError(f"item exceeds configured size limit: {item.id} ({len(encoded)} > {max_item_bytes})")
    kind_dir = "papers" if item.kind == "paper" else "blogs"
    path = root / "items" / kind_dir / f"{item.published_at.year:04d}" / f"{item.published_at.month:02d}" / f"{item.id}.json"
    write_json(path, item.model_dump(mode="json"))


def _digest_entries(items: list[PaperItem | BlogItem]) -> list[DigestEntry]:
    return [
        DigestEntry(
            item_id=item.id,
            rank=rank,
            recommendation_reason_zh=f"与推荐系统研究方向相关：{item.summary_zh}",
        )
        for rank, item in enumerate(items, 1)
    ]


def _attach_provenance(items: list[ContentItem], config: AppConfig, generated_at: datetime) -> None:
    profile = config.models.text.active_profile
    model = config.models.text.active().model
    for item in items:
        if item.llm is None:
            item.llm = LLMMetadata(profile=profile, model=model, generated_at=generated_at)


def integrate(
    stages: StageInputs,
    output: Path,
    config: AppConfig,
    state: State | dict[str, Any] | None = None,
) -> PublishBundle:
    """Create exactly one atomic publish bundle from three matching stages."""
    stage_manifests = [_manifest(stages.stage1), _manifest(stages.paper), _manifest(stages.blog)]
    run_id = stage_manifests[0].run_id
    schema_version = stage_manifests[0].schema_version
    if any(manifest.run_id != run_id for manifest in stage_manifests[1:]):
        raise ValueError("stage manifests must use the same run_id")
    if any(manifest.schema_version != schema_version for manifest in stage_manifests[1:]):
        raise ValueError("stage manifests must use the same schema_version")

    metadata = _candidate_metadata(stages.stage1)
    source_states = _source_states(stages.stage1)
    paper_items = _items(stages.paper, "paper", config.topics, metadata)
    blog_items = _items(stages.blog, "blog", config.topics, metadata)
    all_items = [*paper_items, *blog_items]
    item_ids = [item.id for item in all_items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("duplicate canonical item id")
    allowed_relation_targets = set(item_ids) | {
        entry.id
        for category in (config.topics.targets, config.topics.scenarios, config.topics.tasks, config.topics.methods)
        for entry in category
    }
    for item in all_items:
        for relation in item.graph_relations:
            if relation.target_id not in allowed_relation_targets:
                raise ValueError(f"unknown graph relation target: {relation.target_id}")

    max_deep_reads = config.settings.limits.deep_reading_candidates_per_type
    paper_success_rate = _structured_success_rate(metadata, paper_items, "paper", max_deep_reads)
    blog_success_rate = _structured_success_rate(metadata, blog_items, "blog", max_deep_reads)
    minimum_success_rate = config.settings.structured_analysis_min_success_rate
    if paper_success_rate < minimum_success_rate or blog_success_rate < minimum_success_rate:
        raise ValueError(
            "structured analysis success rate below configured minimum: "
            f"paper={paper_success_rate:.3f}, blog={blog_success_rate:.3f}, minimum={minimum_success_rate:.3f}"
        )
    run_at = datetime.now(UTC)
    _attach_provenance(all_items, config, run_at)
    papers = rank_items(
        paper_items[:max_deep_reads], "paper", config.settings.daily_target,
        final_weights=config.settings.final_weights,
    )
    blogs = rank_items(
        blog_items[:max_deep_reads], "blog", config.settings.daily_target,
        final_weights=config.settings.final_weights,
    )
    digest = Digest(date=run_at.date(), papers=_digest_entries(papers), blogs=_digest_entries(blogs))
    previous = _load_previous_state(state)
    recommended_ids = [entry.item_id for entry in [*digest.papers, *digest.blogs]]
    pending_state = State(
        schema_version=previous.schema_version if previous else schema_version,
        last_success_at=run_at,
        sources={**(previous.sources if previous else {}), **source_states},
        recommended_item_ids=recommended_ids,
        updated_at=run_at,
    )
    report = RunReport(
        run_id=run_id,
        started_at=run_at,
        completed_at=run_at,
        paper_candidates=len(paper_items),
        blog_candidates=len(blog_items),
        paper_recommendations=len(papers),
        blog_recommendations=len(blogs),
        structured_analysis_success_rate=min(paper_success_rate, blog_success_rate),
    )
    manifest = Manifest(run_id=run_id, schema_version=schema_version)

    output = output.resolve()
    if output.exists():
        raise FileExistsError(f"publish bundle already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        pending = temp_path / "pending-data"
        write_json(temp_path / "manifest.json", manifest.model_dump())
        write_json(temp_path / "taxonomy.json", config.topics.to_public_snapshot())
        for item in all_items:
            _write_item(pending, item, config.settings.storage.max_item_bytes)
        digest_path = pending / "digests" / f"{digest.date.year:04d}" / f"{digest.date.month:02d}" / f"{digest.date.isoformat()}.json"
        write_json(digest_path, digest.model_dump(mode="json"))
        report_path = pending / "runs" / f"{run_at.year:04d}" / f"{run_at.month:02d}" / f"{run_id}.json"
        write_json(report_path, report.model_dump(mode="json"))
        write_json(pending / "state.json", pending_state.model_dump(mode="json"))
        final_names = {entry.name for entry in temp_path.iterdir()}
        if final_names != {"manifest.json", "taxonomy.json", "pending-data"}:
            raise ValueError("publish bundle contains an unexpected top-level file")
        temp_path.replace(output)
    except Exception:
        shutil.rmtree(temp_path, ignore_errors=True)
        raise
    return PublishBundle(path=output, manifest=manifest)


def load_digest(bundle: PublishBundle | Path) -> Digest:
    root = bundle.path if isinstance(bundle, PublishBundle) else bundle
    files = sorted((root / "pending-data" / "digests").rglob("*.json"))
    if not files:
        raise FileNotFoundError("publish bundle has no digest")
    return Digest.model_validate(read_json(files[-1]))
