"""Shared collection, filtering, metadata, and Stage 1 artifact orchestration."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Any

from .artifacts import write_json, write_jsonl
from .collect import Candidate, CollectionResult, FeedResponse, collect_candidates, stable_id
from .config import AppConfig
from .filtering import prefilter
from .metadata import MetadataResult, analyze_metadata
from .schemas import BlogItem, Digest, PaperItem, SourceRunStatus, SourceState, Stage1Metadata, StageReport, State
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


def load_history_ids(
    data_root: Path,
    config: AppConfig,
    state: State | dict[str, Any] | None,
) -> set[str]:
    """Load every historically recommended ID from canonical repository data."""
    try:
        current_state = _state_value(state)
    except Exception as exc:
        raise ValueError(f"invalid canonical history state: {exc}") from exc
    history = set(current_state.recommended_item_ids if current_state else [])
    if not data_root.exists():
        return history

    canonical_kinds: dict[str, tuple[str, str]] = {}
    canonical_ids_by_kind: dict[str, set[str]] = {"paper": set(), "blog": set()}
    for path in _canonical_json_files(data_root / "items", data_root):
        relative = path.relative_to(data_root)
        parts = relative.parts
        if (
            len(parts) != 5
            or parts[0] != "items"
            or parts[1] not in {"papers", "blogs"}
            or not re.fullmatch(r"\d{4}", parts[2])
            or not re.fullmatch(r"(?:0[1-9]|1[0-2])", parts[3])
            or path.suffix != ".json"
        ):
            raise ValueError(f"invalid canonical history item path {relative.as_posix()}")
        value = _read_canonical_object(path, data_root)
        try:
            item_type = PaperItem if parts[1] == "papers" else BlogItem
            item = item_type.model_validate(value, context={"taxonomy": config.topics})
            expected_kind = "paper" if parts[1] == "papers" else "blog"
            if item.kind != expected_kind or item.id != path.stem:
                raise ValueError("item kind or stable ID does not match its canonical path")
            if (f"{item.published_at.year:04d}", f"{item.published_at.month:02d}") != parts[2:4]:
                raise ValueError("item publication date does not match its canonical path")
            if isinstance(item, BlogItem) and item.excerpt is not None and len(item.excerpt) > config.settings.storage.max_blog_excerpt_chars:
                raise ValueError("blog excerpt exceeds configured max_blog_excerpt_chars")
        except Exception as exc:
            raise ValueError(f"invalid canonical history item {relative.as_posix()}: {exc}") from exc
        relative_name = relative.as_posix()
        previous = canonical_kinds.get(item.id)
        if previous is not None:
            raise ValueError(
                f"duplicate canonical history item {item.id}: {previous[0]} and {relative_name}"
            )
        kind = "paper" if item.kind == "paper" else "blog"
        canonical_kinds[item.id] = (relative_name, kind)
        canonical_ids_by_kind[kind].add(item.id)

    for path in _canonical_json_files(data_root / "digests", data_root):
        relative = path.relative_to(data_root)
        parts = relative.parts
        if (
            len(parts) != 4
            or parts[0] != "digests"
            or not re.fullmatch(r"\d{4}", parts[1])
            or not re.fullmatch(r"(?:0[1-9]|1[0-2])", parts[2])
            or path.suffix != ".json"
        ):
            raise ValueError(f"invalid canonical history digest path {relative.as_posix()}")
        value = _read_canonical_object(path, data_root)
        try:
            digest = Digest.model_validate(value)
            if (f"{digest.date.year:04d}", f"{digest.date.month:02d}", f"{digest.date.isoformat()}.json") != parts[1:4]:
                raise ValueError("digest date does not match its canonical path")
            missing_papers = [entry.item_id for entry in digest.papers if entry.item_id not in canonical_ids_by_kind["paper"]]
            missing_blogs = [entry.item_id for entry in digest.blogs if entry.item_id not in canonical_ids_by_kind["blog"]]
            if missing_papers or missing_blogs:
                references = [*(f"paper:{item_id}" for item_id in missing_papers), *(f"blog:{item_id}" for item_id in missing_blogs)]
                raise ValueError(f"digest reference does not match canonical item kind: {', '.join(references)}")
        except Exception as exc:
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


def shortlist_candidates(
    candidates: Sequence[Candidate],
    metadata: MetadataResult,
    max_per_kind: int,
) -> list[Candidate]:
    """Select the metadata-ranked deep-reading candidates deterministically."""
    metadata_by_id = {item.id: item for item in metadata.items}

    def key(candidate: Candidate) -> tuple[float, float, str, str]:
        item = metadata_by_id[stable_id(candidate)]
        return (-item.relevance_score, -candidate.published_at.timestamp(), candidate.source_id, stable_id(candidate))

    selected: list[Candidate] = []
    for kind in ("paper", "blog"):
        selected.extend(sorted((item for item in candidates if item.kind == kind), key=key)[:max_per_kind])
    return selected


def collection_stage_report(config: AppConfig, result: CollectionResult, metadata: MetadataResult) -> StageReport:
    warnings = list(result.warnings)
    sources: list[SourceRunStatus] = []
    for source in [*config.sources.academic, *config.sources.blogs]:
        if not source.enabled:
            continue
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
    candidates = shortlist_candidates(
        candidates,
        metadata,
        config.settings.limits.deep_reading_candidates_per_type,
    )
    write_stage_one(
        output,
        run_id or current.strftime("%Y%m%dT%H%M%SZ"),
        candidates,
        result.source_states,
        metadata,
        collection_stage_report(config, result, metadata),
    )
    return candidates
