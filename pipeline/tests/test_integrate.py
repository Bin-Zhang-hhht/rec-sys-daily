from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from recsys_daily.config import load_config
from recsys_daily.integrate import StageInputs, integrate, load_digest
from recsys_daily.ranking import rank_items
from recsys_daily.schemas import BuildConfigSnapshot, PaperItem, RunReport, SourceState, StageReport, State


ROOT = Path(__file__).parents[2]
CONFIG = load_config(ROOT)
PUBLISHED_AT = datetime(2026, 8, 9, tzinfo=UTC)


def _paper(item_id: str, score: float) -> dict[str, object]:
    return {
        "kind": "paper",
        "id": item_id,
        "title": f"Paper {item_id}",
        "summary_zh": f"Summary for {item_id}",
        "source": "arxiv",
        "url": f"https://arxiv.org/abs/{item_id}",
        "published_at": PUBLISHED_AT.isoformat().replace("+00:00", "Z"),
        "authors": ["Author"],
        "targets": ["content"],
        "scenarios": ["text_feed"],
        "tasks": ["ranking"],
        "methods": ["two_tower"],
        "relevance_score": score,
        "deep_reading": {
            "analysis_basis": "pdf_text",
            "visual_analysis": {"status": "not_required"},
        },
    }


def _blog(item_id: str, score: float) -> dict[str, object]:
    return {
        "kind": "blog",
        "id": item_id,
        "title": f"Blog {item_id}",
        "summary_zh": f"Summary for {item_id}",
        "source": "meta_engineering",
        "url": f"https://example.com/{item_id}",
        "published_at": PUBLISHED_AT.isoformat().replace("+00:00", "Z"),
        "authors": ["Author"],
        "targets": ["content"],
        "scenarios": ["text_feed"],
        "tasks": ["ranking"],
        "methods": ["two_tower"],
        "relevance_score": score,
        "deep_reading": {
            "analysis_basis": "excerpt_fallback",
            "system_context_zh": "context",
        },
    }


def _write_stage(path: Path, run_id: str, kind: str, items: list[dict[str, object]]) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"run_id": run_id, "schema_version": "1"}), encoding="utf-8")
    (path / "items.jsonl").write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return path


def fixture_stages(tmp_path: Path, *, paper_run_id: str = "run-1", blog_run_id: str | None = None) -> StageInputs:
    blog_run_id = blog_run_id or paper_run_id
    stage1 = tmp_path / "stage-1"
    stage1.mkdir()
    (stage1 / "manifest.json").write_text(json.dumps({"run_id": paper_run_id, "schema_version": "1"}), encoding="utf-8")
    candidates = [_paper(f"paper-{i}", 1 - i / 20) for i in range(10)] + [_blog(f"blog-{i}", 1 - i / 20) for i in range(10)]
    (stage1 / "items.jsonl").write_text(
        "".join(json.dumps({**{key: value for key, value in item.items() if key not in {"deep_reading"}}, "graph_relations": [], "degraded": False}) + "\n" for item in candidates),
        encoding="utf-8",
    )
    (stage1 / "source-states.json").write_text(
        json.dumps({"arxiv": SourceState(last_success_at=PUBLISHED_AT).model_dump(mode="json")}),
        encoding="utf-8",
    )
    (stage1 / "stage-report.json").write_text(json.dumps(StageReport().model_dump(mode="json")), encoding="utf-8")
    _write_stage(tmp_path / "paper", paper_run_id, "paper", [_paper(f"paper-{i}", 1 - i / 20) for i in range(10)])
    _write_stage(tmp_path / "blog", blog_run_id, "blog", [_blog(f"blog-{i}", 1 - i / 20) for i in range(10)])
    return StageInputs(stage1=stage1, paper=tmp_path / "paper", blog=tmp_path / "blog")


def test_publish_bundle_allowlist(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    assert sorted(path.name for path in bundle.path.iterdir()) == ["manifest.json", "pending-data", "taxonomy.json"]


def test_integration_does_not_write_empty_digest_but_keeps_report_and_state(tmp_path: Path) -> None:
    settings = CONFIG.settings.model_copy(update={"minimum_final_score": 1.0})
    config = CONFIG.model_copy(update={"settings": settings})
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", config, state=None)

    pending = bundle.path / "pending-data"
    assert not list((pending / "digests").rglob("*.json")) if (pending / "digests").exists() else True
    assert list((pending / "runs").rglob("*.json"))
    assert (pending / "state.json").exists()


def test_mismatched_manifest_is_rejected_without_state(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path, paper_run_id="a", blog_run_id="b")
    with pytest.raises(ValueError, match="run_id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)
    assert not (tmp_path / "bundle" / "pending-data" / "state.json").exists()


def test_digest_references_ids_and_caps_each_kind(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    digest = load_digest(bundle)
    assert len(digest.papers) <= 8
    assert len(digest.blogs) <= 8
    assert all(isinstance(entry.item_id, str) for entry in digest.papers + digest.blogs)
    item_paths = list((bundle.path / "pending-data" / "items").rglob("*.json"))
    item_ids = {json.loads(path.read_text(encoding="utf-8"))["id"] for path in item_paths}
    assert {entry.item_id for entry in digest.papers + digest.blogs} <= item_ids


def test_source_states_are_carried_into_pending_state_and_provenance_is_recorded(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    (stages.stage1 / "source-states.json").write_text(
        json.dumps({"arxiv": SourceState(etag='"new"', last_success_at=PUBLISHED_AT).model_dump(mode="json")}),
        encoding="utf-8",
    )
    previous = State(sources={"blog": SourceState(etag='"old"')})

    bundle = integrate(stages, tmp_path / "bundle", CONFIG, state=previous)
    state = json.loads((bundle.path / "pending-data" / "state.json").read_text(encoding="utf-8"))
    assert state["sources"]["arxiv"]["etag"] == '"new"'
    assert state["sources"]["blog"]["etag"] == '"old"'
    item_path = next((bundle.path / "pending-data" / "items").rglob("*.json"))
    item = json.loads(item_path.read_text(encoding="utf-8"))
    assert item["llm"]["profile"] == CONFIG.models.text.active_profile
    assert item["llm"]["model"] == CONFIG.models.text.active().model


def test_deep_read_ids_must_belong_to_stage_one_candidates(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "items.jsonl"
    value = json.loads(paper_path.read_text(encoding="utf-8").splitlines()[0])
    value["id"] = "unknown-paper"
    paper_path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="deep-reading id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_final_ranking_uses_configured_deep_read_dimensions() -> None:
    low = PaperItem.model_validate(_paper("low", 0.95), context={"taxonomy": CONFIG.topics})
    high_data = _paper("high", 0.60)
    high_data["deep_reading"] = {
        "analysis_basis": "pdf_text",
        "visual_analysis": {"status": "not_required"},
        "evidence_quality": 1.0,
        "business_transferability": 1.0,
        "technical_depth": 1.0,
    }
    high = PaperItem.model_validate(high_data, context={"taxonomy": CONFIG.topics})
    assert rank_items([low, high], "paper", 8, final_weights=CONFIG.settings.final_weights)[0].id == "high"


def test_integration_rejects_low_structured_analysis_rate(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    stage_items = [json.loads(line) for line in (stages.paper / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    (stages.paper / "items.jsonl").write_text(json.dumps(stage_items[0]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="structured analysis success rate"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_integration_rejects_item_over_storage_limit(tmp_path: Path) -> None:
    storage = CONFIG.settings.storage.model_copy(update={"max_item_bytes": 100})
    settings = CONFIG.settings.model_copy(update={"storage": storage})
    config = CONFIG.model_copy(update={"settings": settings})
    with pytest.raises(ValueError, match="max_item_bytes"):
        integrate(fixture_stages(tmp_path), tmp_path / "bundle", config, state=None)


def test_source_states_are_promoted_into_pending_state(tmp_path: Path) -> None:
    previous = State(sources={"old": SourceState(cursor="keep")})

    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=previous)

    state = json.loads((bundle.path / "pending-data" / "state.json").read_text(encoding="utf-8"))
    assert state["sources"]["arxiv"]["last_success_at"] == "2026-08-09T00:00:00Z"
    assert state["sources"]["old"]["cursor"] == "keep"


def test_unknown_deep_read_candidate_id_is_rejected(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "items.jsonl"
    value = json.loads(paper_path.read_text(encoding="utf-8").splitlines()[0])
    value["id"] = "paper-not-collected"
    paper_path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="candidate id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_structured_analysis_success_rate_is_enforced(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "items.jsonl"
    lines = paper_path.read_text(encoding="utf-8").splitlines()[:8]
    paper_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="structured analysis success rate"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_item_size_limit_is_enforced_before_publish(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.stage1 / "items.jsonl"
    lines = paper_path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[0])
    value["summary_zh"] = "x" * (CONFIG.settings.storage.max_item_bytes + 1)
    lines[0] = json.dumps(value)
    paper_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="item exceeds configured size"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)
    assert not (tmp_path / "bundle").exists()


def test_integrate_copies_historical_json_tree_and_merges_recommended_ids(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    stages = fixture_stages(run_root)
    data = tmp_path / "data"
    historical_item = data / "items" / "papers" / "2025" / "01" / "historical-paper.json"
    historical_digest = data / "digests" / "2025" / "01" / "2025-01-02.json"
    historical_run = data / "runs" / "2025" / "01" / "historical-run.json"
    historical_item_value = _paper("historical-paper", 0.5)
    historical_item_value["published_at"] = "2025-01-02T00:00:00Z"
    snapshot = BuildConfigSnapshot(
        graph_max_content_nodes=CONFIG.settings.graph_max_content_nodes,
        graph_recent_days=CONFIG.settings.graph_recent_days,
        minimum_final_score=CONFIG.settings.minimum_final_score,
        target_item_bytes=CONFIG.settings.storage.target_item_bytes,
        max_item_bytes=CONFIG.settings.storage.max_item_bytes,
        max_blog_excerpt_chars=CONFIG.settings.storage.max_blog_excerpt_chars,
        warn_repository_data_mb=CONFIG.settings.storage.warn_repository_data_mb,
        warn_pages_artifact_mb=CONFIG.settings.storage.warn_pages_artifact_mb,
        fail_pages_artifact_mb=CONFIG.settings.storage.fail_pages_artifact_mb,
    )
    for path, value in (
        (historical_item, historical_item_value),
        (historical_digest, {"date": "2025-01-02", "papers": [{"item_id": "historical-paper", "recommendation_reason_zh": "历史推荐", "rank": 1}], "blogs": []}),
        (historical_run, RunReport(run_id="historical-run", started_at=datetime(2025, 1, 2, tzinfo=UTC), config_snapshot=snapshot, stage_report=StageReport()).model_dump(mode="json")),
        (data / "state.json", State(recommended_item_ids=["historical-paper"]).model_dump(mode="json")),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    bundle = integrate(
        stages,
        tmp_path / "bundle",
        CONFIG,
        state=State(recommended_item_ids=["historical-paper", "paper-0"]),
        repository_data=data,
    )
    pending = bundle.path / "pending-data"
    assert (pending / "items/papers/2025/01/historical-paper.json").exists()
    assert (pending / "digests/2025/01/2025-01-02.json").exists()
    assert (pending / "runs/2025/01/historical-run.json").exists()
    state = json.loads((pending / "state.json").read_text(encoding="utf-8"))
    assert state["recommended_item_ids"][:2] == ["historical-paper", "paper-0"]
    report_path = next((pending / "runs").rglob("run-1.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["config_snapshot"]["graph_recent_days"] == CONFIG.settings.graph_recent_days
    assert report["stage_report"]["metadata_llm_success_rate"] == 1.0


def test_integrate_rejects_invalid_historical_json_transactionally(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    stages = fixture_stages(run_root)
    invalid = tmp_path / "data/items/papers/2025/01/invalid.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text(json.dumps({"id": "invalid"}), encoding="utf-8")

    with pytest.raises(ValueError, match="historical|canonical"):
        integrate(stages, tmp_path / "bundle", CONFIG, repository_data=tmp_path / "data")

    assert not (tmp_path / "bundle").exists()


def test_integrate_rejects_historical_digest_with_missing_item_transactionally(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    stages = fixture_stages(run_root)
    digest = tmp_path / "data/digests/2025/01/2025-01-02.json"
    digest.parent.mkdir(parents=True)
    digest.write_text(
        json.dumps({
            "date": "2025-01-02",
            "papers": [{"item_id": "missing-paper", "recommendation_reason_zh": "历史推荐", "rank": 1}],
            "blogs": [],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="digest references missing canonical item"):
        integrate(stages, tmp_path / "bundle", CONFIG, repository_data=tmp_path / "data")

    assert not (tmp_path / "bundle").exists()


def test_integrate_enforces_configured_blog_excerpt_limit(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    stage_path = stages.stage1 / "items.jsonl"
    values = [json.loads(line) for line in stage_path.read_text(encoding="utf-8").splitlines()]
    next(item for item in values if item["kind"] == "blog")["excerpt"] = "12345678"
    stage_path.write_text("\n".join(json.dumps(item) for item in values) + "\n", encoding="utf-8")
    storage = CONFIG.settings.storage.model_copy(update={"max_blog_excerpt_chars": 7})
    settings = CONFIG.settings.model_copy(update={"storage": storage})
    config = CONFIG.model_copy(update={"settings": settings})

    with pytest.raises(ValueError, match="max_blog_excerpt_chars"):
        integrate(stages, tmp_path / "bundle", config)


def test_integrate_rejects_unsupported_repository_data_transactionally(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    stages = fixture_stages(run_root)
    data = tmp_path / "data"
    invalid = data / "items" / "papers" / "2025" / "01" / "source.txt"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported file"):
        integrate(stages, tmp_path / "bundle", CONFIG, repository_data=data)
    assert not (tmp_path / "bundle").exists()


def test_integrate_rejects_low_stage_metadata_success_rate(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    (stages.stage1 / "stage-report.json").write_text(
        json.dumps(StageReport(metadata_llm_success_rate=0.5).model_dump(mode="json")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="metadata analysis success rate"):
        integrate(stages, tmp_path / "bundle", CONFIG)
