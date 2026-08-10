"""Validate stage outputs and assemble an atomic publish bundle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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
    PaperItem,
    RunReport,
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


def _items(path: Path, kind: str, taxonomy: Any) -> list[ContentItem]:
    candidates = [path / "items.jsonl", path / f"{kind}s.jsonl", path / f"{kind}.jsonl"]
    source = next((candidate for candidate in candidates if candidate.exists()), None)
    if source is None:
        raise ValueError(f"missing {kind} deep-reading artifact in {path}")
    parsed: list[ContentItem] = []
    for value in read_jsonl(source):
        item_type = PaperItem if kind == "paper" else BlogItem
        parsed.append(item_type.model_validate(value, context={"taxonomy": taxonomy}))
    return parsed


def _load_previous_state(value: State | dict[str, Any] | None) -> State | None:
    if value is None:
        return None
    return value if isinstance(value, State) else State.model_validate(value)


def _write_item(root: Path, item: PaperItem | BlogItem) -> None:
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

    paper_items = _items(stages.paper, "paper", config.topics)
    blog_items = _items(stages.blog, "blog", config.topics)
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
    papers = rank_items(paper_items[:max_deep_reads], "paper", config.settings.daily_target)
    blogs = rank_items(blog_items[:max_deep_reads], "blog", config.settings.daily_target)
    run_at = datetime.now(UTC)
    digest = Digest(date=run_at.date(), papers=_digest_entries(papers), blogs=_digest_entries(blogs))
    previous = _load_previous_state(state)
    recommended_ids = [entry.item_id for entry in [*digest.papers, *digest.blogs]]
    pending_state = State(
        schema_version=previous.schema_version if previous else schema_version,
        last_success_at=run_at,
        sources=previous.sources if previous else {},
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
            _write_item(pending, item)
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
