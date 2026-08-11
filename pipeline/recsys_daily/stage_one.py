"""Shared collection, filtering, metadata, and Stage 1 artifact orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from .artifacts import write_json, write_jsonl
from .collect import Candidate, CollectionResult, FeedResponse, collect_candidates, stable_id
from .config import AppConfig
from .filtering import prefilter
from .metadata import MetadataResult, analyze_metadata
from .schemas import Digest, SourceRunStatus, SourceState, Stage1Metadata, StageReport, State
from .security import Resolver


CompleteJson = Callable[[Sequence[Mapping[str, Any]], Mapping[str, Any]], Mapping[str, Any]]
FeedFetcher = Callable[[str, Mapping[str, str]], FeedResponse]


def _state_value(state: State | dict[str, Any] | None) -> State | None:
    if state is None:
        return None
    if isinstance(state, State):
        return state
    return State.model_validate(state)


def _read_canonical_object(path: Path, data_root: Path) -> dict[str, Any]:
    relative = path.relative_to(data_root).as_posix()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid canonical history JSON {relative}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"invalid canonical history JSON {relative}: expected an object")
    return value


def _canonical_json_files(root: Path, data_root: Path) -> list[Path]:
    if not root.exists():
        return []
    files = sorted(path for path in root.rglob("*") if path.is_file())
    unsupported = next((path for path in files if path.suffix != ".json"), None)
    if unsupported is not None:
        relative = unsupported.relative_to(data_root).as_posix()
        raise ValueError(f"invalid canonical history file {relative}: expected JSON")
    return files


def load_history_ids(data_root: Path, state: State | dict[str, Any] | None) -> set[str]:
    """Load every historically recommended ID from canonical repository data."""
    try:
        current_state = _state_value(state)
    except Exception as exc:
        raise ValueError(f"invalid canonical history state: {exc}") from exc
    history = set(current_state.recommended_item_ids if current_state else [])
    if not data_root.exists():
        return history

    for path in _canonical_json_files(data_root / "items", data_root):
        value = _read_canonical_object(path, data_root)
        item_id = value.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            relative = path.relative_to(data_root).as_posix()
            raise ValueError(f"invalid canonical history item {relative}: id must be a non-empty string")
        if item_id != path.stem:
            relative = path.relative_to(data_root).as_posix()
            raise ValueError(f"invalid canonical history item {relative}: id does not match its canonical path")
        history.add(item_id)

    for path in _canonical_json_files(data_root / "digests", data_root):
        value = _read_canonical_object(path, data_root)
        try:
            digest = Digest.model_validate(value)
        except Exception as exc:
            relative = path.relative_to(data_root).as_posix()
            raise ValueError(f"invalid canonical history digest {relative}: {exc}") from exc
        history.update(entry.item_id for entry in [*digest.papers, *digest.blogs])
    return history


def candidate_document(candidate: Candidate, metadata: Stage1Metadata | None = None) -> dict[str, Any]:
    value = asdict(candidate)
    value.pop("feed_content", None)
    value["id"] = stable_id(candidate)
    value["published_at"] = candidate.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    for key in ("authors", "categories", "source_scenarios"):
        value[key] = list(value[key])
    if metadata is not None:
        value.update(metadata.model_dump(mode="json"))
    return value


def write_stage_one(
    output: Path,
    run_id: str,
    candidates: list[Candidate],
    source_states: dict[str, SourceState] | None = None,
    metadata: MetadataResult | None = None,
    stage_report: StageReport | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "manifest.json", {"run_id": run_id, "schema_version": "1"})
    metadata_by_id = {item.id: item for item in (metadata.items if metadata else [])}
    for kind in ("paper", "blog"):
        values = [
            candidate_document(candidate, metadata_by_id.get(stable_id(candidate)))
            for candidate in candidates
            if candidate.kind == kind
        ]
        write_jsonl(output / f"{kind}s.jsonl", values)
    write_json(
        output / "source-states.json",
        {source_id: value.model_dump(mode="json") for source_id, value in (source_states or {}).items()},
    )
    write_json(output / "stage-report.json", (stage_report or StageReport()).model_dump(mode="json"))


def collection_stage_report(config: AppConfig, result: CollectionResult, metadata: MetadataResult) -> StageReport:
    warnings = list(result.warnings)
    sources: list[SourceRunStatus] = []
    for source in [*config.sources.academic, *config.sources.blogs]:
        warning = next((item for item in warnings if item.startswith(f"{source.id}:")), None)
        sources.append(SourceRunStatus(source_id=source.id, success=warning is None, warning=warning))
    return StageReport(
        sources=sources,
        warnings=warnings,
        metadata_llm_calls=metadata.llm_calls,
        metadata_llm_success_rate=metadata.success_rate,
        metadata_degraded_count=metadata.degraded_count,
    )


def run_collect_filter(
    config: AppConfig,
    output: Path,
    state: State | dict[str, Any] | None,
    history: State | Iterable[str] | None,
    complete_json: CompleteJson,
    *,
    fetcher: FeedFetcher | None = None,
    resolver: Resolver | None = None,
    now: datetime | None = None,
    run_id: str | None = None,
) -> list[Candidate]:
    """Run the production Stage 1 sequence and persist only bounded artifacts."""
    result = collect_candidates(config, state, now=now, fetcher=fetcher, resolver=resolver)
    current = result.window.until
    candidates = prefilter(result.candidates, config, history, now=current)
    metadata = analyze_metadata(candidates, config, complete_json)
    write_stage_one(
        output,
        run_id or current.strftime("%Y%m%dT%H%M%SZ"),
        candidates,
        result.source_states,
        metadata,
        collection_stage_report(config, result, metadata),
    )
    return candidates
