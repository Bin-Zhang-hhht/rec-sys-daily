from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat

import pytest

from recsys_daily.config import load_config
from recsys_daily.integrate import StageInputs, _business_date, integrate, load_digest
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
        "summary_zh": f"论文 {item_id} 的中文摘要。",
        "source": "arxiv",
        "url": f"https://arxiv.org/abs/{item_id}",
        "published_at": PUBLISHED_AT.isoformat().replace("+00:00", "Z"),
        "authors": ["Author"],
        "abstract": f"Abstract for {item_id}",
        "arxiv_id": item_id,
        "doi": None,
        "targets": ["content"],
        "scenarios": ["text_feed"],
        "tasks": ["ranking"],
        "methods": ["two_tower"],
        "relevance_score": score,
        "deep_reading": {
            "analysis_basis": "mineru_full_text",
        },
    }


def _blog(item_id: str, score: float) -> dict[str, object]:
    return {
        "kind": "blog",
        "id": item_id,
        "title": f"Blog {item_id}",
        "summary_zh": f"博客 {item_id} 的中文摘要。",
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
    (path / f"{kind}-deep-readings.json").write_text(
        json.dumps({"kind": kind, "items": items, "failures": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _write_similarity(path: Path, run_id: str, item_ids: set[str]) -> Path:
    settings = CONFIG.settings.similarity
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "schema_version": "1"}), encoding="utf-8"
    )
    document = {
        "run_id": run_id,
        "schema_version": "1",
        "model": {
            "library": settings.library,
            "version": settings.version,
            "name": settings.model,
            "dimension": settings.dimension,
            "normalized": True,
        },
        "parameters": {
            "max_input_tokens": settings.max_input_tokens,
            "title_tokens": settings.title_tokens,
            "abstract_tokens": settings.abstract_tokens,
            "summary_tokens": settings.summary_tokens,
            "separator_tokens": settings.separator_tokens,
            "top_k": settings.top_k,
            "min_cosine": settings.min_cosine,
            "mutual_top_k": settings.mutual_top_k,
        },
        "items_considered": len(item_ids),
        "encoded_items": len(item_ids),
        "token_counts": [
            {"id": item_id, "title": 1, "abstract": 1, "summary_zh": 1, "total": 4}
            for item_id in sorted(item_ids)
        ],
        "edges": [],
    }
    (path / "similarity.json").write_text(json.dumps(document), encoding="utf-8")
    (path / "similarity-report.json").write_text(json.dumps({
        "run_id": run_id,
        "schema_version": "1",
        "model": document["model"],
        "items_considered": len(item_ids),
        "encoded_items": len(item_ids),
        "truncated_items": 0,
        "edge_count": 0,
        "top_k": settings.top_k,
        "min_cosine": settings.min_cosine,
        "elapsed_seconds": 0.0,
        "cache_status": "not_observed",
    }), encoding="utf-8")
    return path


def _refresh_similarity_for_current_results(stages: StageInputs, *, paper_count: int) -> None:
    paper_document = json.loads((stages.paper / "paper-deep-readings.json").read_text(encoding="utf-8"))
    blog_document = json.loads((stages.blog / "blog-deep-readings.json").read_text(encoding="utf-8"))
    stage_one = {
        value["id"]: value
        for value in (
            json.loads(line)
            for line in (stages.stage1 / "papers.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }
    paper_ids = {
        value["id"]
        for value in paper_document["items"][:paper_count]
        if not (stage_one[value["id"]].get("degraded") and not any("\u4e00" <= char <= "\u9fff" for char in stage_one[value["id"]].get("summary_zh", "")))
    }
    blog_ids = {value["id"] for value in blog_document["items"]}
    _write_similarity(stages.similarity, "run-1", paper_ids | blog_ids)


def fixture_stages(tmp_path: Path, *, paper_run_id: str = "run-1", blog_run_id: str | None = None) -> StageInputs:
    blog_run_id = blog_run_id or paper_run_id
    stage1 = tmp_path / "stage-1"
    stage1.mkdir()
    (stage1 / "manifest.json").write_text(json.dumps({"run_id": paper_run_id, "schema_version": "1"}), encoding="utf-8")
    candidates = [_paper(f"paper-{i}", 1 - i / 20) for i in range(10)] + [_blog(f"blog-{i}", 1 - i / 20) for i in range(10)]

    def stage1_record(item: dict[str, object]) -> dict[str, object]:
        record = {
            **{key: value for key, value in item.items() if key not in {"deep_reading"}},
            "degraded": False,
        }
        if record["kind"] == "paper":
            record["excerpt"] = record.pop("abstract")
        return record

    (stage1 / "papers.jsonl").write_text(
        "".join(json.dumps(stage1_record(item)) + "\n" for item in candidates if item["kind"] == "paper"),
        encoding="utf-8",
    )
    (stage1 / "blogs.jsonl").write_text(
        "".join(json.dumps(stage1_record(item)) + "\n" for item in candidates if item["kind"] == "blog"),
        encoding="utf-8",
    )
    (stage1 / "source-states.json").write_text(
        json.dumps({"arxiv": SourceState(last_success_at=PUBLISHED_AT).model_dump(mode="json")}),
        encoding="utf-8",
    )
    (stage1 / "stage-report.json").write_text(json.dumps(StageReport().model_dump(mode="json")), encoding="utf-8")
    _write_stage(tmp_path / "paper", paper_run_id, "paper", [_paper(f"paper-{i}", 1 - i / 20) for i in range(10)])
    _write_stage(tmp_path / "blog", blog_run_id, "blog", [_blog(f"blog-{i}", 1 - i / 20) for i in range(10)])
    similarity = _write_similarity(tmp_path / "similarity", paper_run_id, {str(item["id"]) for item in candidates})
    return StageInputs(stage1=stage1, paper=tmp_path / "paper", blog=tmp_path / "blog", similarity=similarity)


def test_publish_bundle_allowlist(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    assert sorted(path.name for path in bundle.path.iterdir()) == ["manifest.json", "pending-data", "taxonomy.json"]


def test_integrate_rejects_stage_one_candidate_overflow(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    papers_path = stages.stage1 / "papers.jsonl"
    extra_papers = [_paper(f"paper-{index}", 0.1) for index in range(10, 22)]
    with papers_path.open("a", encoding="utf-8") as stream:
        for item in extra_papers:
            record = {key: value for key, value in item.items() if key != "deep_reading"}
            record["excerpt"] = record.pop("abstract")
            record.update({"degraded": False})
            stream.write(json.dumps(record) + "\n")
    paper_deep_read_path = stages.paper / "paper-deep-readings.json"
    paper_deep_read = json.loads(paper_deep_read_path.read_text(encoding="utf-8"))
    paper_deep_read["items"].extend(extra_papers)
    paper_deep_read_path.write_text(json.dumps(paper_deep_read), encoding="utf-8")

    with pytest.raises(ValueError, match="stage-1 paper candidate count exceeds"):
        integrate(stages, tmp_path / "bundle", CONFIG)


def test_removed_graph_relations_field_is_rejected(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    papers = stages.stage1 / "papers.jsonl"
    lines = papers.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["graph_relations"] = [
        {
            "type": "related",
            "target_id": "url-unknown-hash",
            "confidence": 0.9,
            "evidence": "mentions the item",
            "generated_by": "metadata",
        },
        {
            "type": "applies",
            "target_id": "two_tower",
            "confidence": 0.8,
            "evidence": "method mentioned",
            "generated_by": "metadata",
        },
    ]
    lines[0] = json.dumps(first)
    papers.write_text("".join(line + "\n" for line in lines), encoding="utf-8")

    with pytest.raises(ValueError, match="graph_relations"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_integrate_requires_output_to_not_exist(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    empty = tmp_path / "bundle"
    empty.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        integrate(stages, empty, CONFIG, state=None)

    populated = tmp_path / "populated"
    populated.mkdir()
    (populated / "existing.json").write_text("{}", encoding="utf-8")
    again = tmp_path / "again"
    again.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        integrate(fixture_stages(again), populated, CONFIG, state=None)


def test_integrate_publishes_a_traversable_bundle_root(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)

    assert bundle.path.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(bundle.path.stat().st_mode) == 0o755


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


def test_invalid_similarity_artifact_blocks_publish_bundle(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    similarity_path = stages.similarity / "similarity.json"
    document = json.loads(similarity_path.read_text(encoding="utf-8"))
    document["encoded_items"] -= 1
    similarity_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="full canonical input"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert not (tmp_path / "bundle").exists()


def test_similarity_rank_score_inversion_blocks_publish_bundle(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    similarity_path = stages.similarity / "similarity.json"
    document = json.loads(similarity_path.read_text(encoding="utf-8"))
    document["edges"] = [
        {"source_id": "paper-0", "target_id": "paper-1", "score": 0.9, "source_rank": 2, "target_rank": 1},
        {"source_id": "paper-0", "target_id": "paper-2", "score": 0.8, "source_rank": 1, "target_rank": 1},
    ]
    similarity_path.write_text(json.dumps(document), encoding="utf-8")
    report_path = stages.similarity / "similarity-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["edge_count"] = 2
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="rank/score order"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert not (tmp_path / "bundle").exists()


def test_digest_references_ids_and_caps_each_kind(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    digest = load_digest(bundle)
    assert len(digest.papers) <= 10
    assert len(digest.blogs) <= 10
    assert all(isinstance(entry.item_id, str) for entry in digest.papers + digest.blogs)
    item_paths = list((bundle.path / "pending-data" / "items").rglob("*.json"))
    item_ids = {json.loads(path.read_text(encoding="utf-8"))["id"] for path in item_paths}
    assert {entry.item_id for entry in digest.papers + digest.blogs} <= item_ids


def test_digest_business_date_uses_asia_shanghai() -> None:
    assert _business_date(datetime(2026, 8, 18, 15, 59, tzinfo=UTC)).isoformat() == "2026-08-18"
    assert _business_date(datetime(2026, 8, 18, 16, 0, tzinfo=UTC)).isoformat() == "2026-08-19"


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
    assert set(item["llm"]) == {"model", "generated_at", "degraded"}
    assert item["llm"]["model"] == CONFIG.models.text.model


def test_deep_read_ids_must_belong_to_stage_one_candidates(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "paper-deep-readings.json"
    paper_document = json.loads(paper_path.read_text(encoding="utf-8"))
    value = paper_document["items"][0]
    value["id"] = "unknown-paper"
    paper_document["items"][0] = value
    paper_path.write_text(json.dumps(paper_document), encoding="utf-8")
    with pytest.raises(ValueError, match="deep-reading id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_final_ranking_uses_configured_deep_read_dimensions() -> None:
    low = PaperItem.model_validate(_paper("low", 0.95), context={"taxonomy": CONFIG.topics})
    high_data = _paper("high", 0.60)
    high_data["deep_reading"] = {
        "analysis_basis": "mineru_full_text",
        "evidence_quality": 1.0,
        "business_transferability": 1.0,
        "technical_depth": 1.0,
    }
    high = PaperItem.model_validate(high_data, context={"taxonomy": CONFIG.topics})
    assert rank_items([low, high], "paper", 8, final_weights=CONFIG.settings.final_weights)[0].id == "high"


def test_integration_rejects_low_structured_analysis_rate(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "paper-deep-readings.json"
    paper_document = json.loads(paper_path.read_text(encoding="utf-8"))
    failed = paper_document["items"][1:]
    paper_document["items"] = paper_document["items"][:1]
    paper_document["failures"] = [{"id": item["id"], "code": "deep_read_failed"} for item in failed]
    paper_path.write_text(json.dumps(paper_document), encoding="utf-8")
    _refresh_similarity_for_current_results(stages, paper_count=1)
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
    paper_path = stages.paper / "paper-deep-readings.json"
    paper_document = json.loads(paper_path.read_text(encoding="utf-8"))
    paper_document["items"][0]["id"] = "paper-not-collected"
    paper_path.write_text(json.dumps(paper_document), encoding="utf-8")

    with pytest.raises(ValueError, match="candidate id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_structured_analysis_success_rate_is_enforced(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "paper-deep-readings.json"
    # Seven of ten Stage 1 paper candidates is below the configured 0.80 gate.
    paper_document = json.loads(paper_path.read_text(encoding="utf-8"))
    failed = paper_document["items"][7:]
    paper_document["items"] = paper_document["items"][:7]
    paper_document["failures"] = [{"id": item["id"], "code": "deep_read_failed"} for item in failed]
    paper_path.write_text(json.dumps(paper_document), encoding="utf-8")
    _refresh_similarity_for_current_results(stages, paper_count=7)

    with pytest.raises(ValueError, match="structured analysis success rate"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)


def test_structured_analysis_accepts_exact_eighty_percent(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    paper_path = stages.paper / "paper-deep-readings.json"
    paper_document = json.loads(paper_path.read_text(encoding="utf-8"))
    failed = paper_document["items"][8:]
    paper_document["items"] = paper_document["items"][:8]
    paper_document["failures"] = [{"id": item["id"], "code": "deep_read_failed"} for item in failed]
    paper_path.write_text(json.dumps(paper_document), encoding="utf-8")
    _refresh_similarity_for_current_results(stages, paper_count=8)

    bundle = integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert bundle.path.exists()


def test_item_size_limit_is_enforced_before_publish(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    blob_path = stages.stage1 / "papers.jsonl"
    lines = blob_path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[0])
    value["summary_zh"] = "中" * (CONFIG.settings.storage.max_item_bytes + 1)
    lines[0] = json.dumps(value)
    blob_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="item exceeds configured size"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)
    assert not (tmp_path / "bundle").exists()


def test_integrate_rejects_non_chinese_stage_one_summary(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    papers_path = stages.stage1 / "papers.jsonl"
    lines = papers_path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[0])
    value["summary_zh"] = "English summary that bypassed Stage 1 validation."
    lines[0] = json.dumps(value)
    papers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="contains no CJK text"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert not (tmp_path / "bundle").exists()


def test_integrate_skips_degraded_non_chinese_summary(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    papers_path = stages.stage1 / "papers.jsonl"
    lines = papers_path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[0])
    value["summary_zh"] = "English degraded fallback summary."
    value["degraded"] = True
    lines[0] = json.dumps(value)
    papers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh_similarity_for_current_results(stages, paper_count=10)

    bundle = integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert not (bundle.path / "pending-data/items/papers/2026/08/paper-0.json").exists()
    assert all(entry.item_id != "paper-0" for entry in load_digest(bundle).papers)


def test_integrate_accepts_degraded_item_with_one_grounded_label(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    papers_path = stages.stage1 / "papers.jsonl"
    lines = papers_path.read_text(encoding="utf-8").splitlines()
    value = json.loads(lines[0])
    value["degraded"] = True
    value["scenarios"] = []
    value["tasks"] = []
    value["methods"] = []
    lines[0] = json.dumps(value)
    papers_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    bundle = integrate(stages, tmp_path / "bundle", CONFIG, state=None)

    assert (bundle.path / "pending-data/items/papers/2026/08/paper-0.json").is_file()


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
        graph_initial_content_nodes=CONFIG.settings.graph_initial_content_nodes,
        graph_shard_target_bytes=CONFIG.settings.graph_shard_target_bytes,
        minimum_final_score=CONFIG.settings.minimum_final_score,
        minimum_metadata_relevance_score=CONFIG.settings.minimum_metadata_relevance_score,
        target_item_bytes=CONFIG.settings.storage.target_item_bytes,
        max_item_bytes=CONFIG.settings.storage.max_item_bytes,
        max_blog_excerpt_chars=CONFIG.settings.storage.max_blog_excerpt_chars,
        warn_repository_data_mb=CONFIG.settings.storage.warn_repository_data_mb,
        warn_pages_artifact_mb=CONFIG.settings.storage.warn_pages_artifact_mb,
        fail_pages_artifact_mb=CONFIG.settings.storage.fail_pages_artifact_mb,
    )
    historical_run_value = RunReport(
        run_id="historical-run",
        started_at=datetime(2025, 1, 2, tzinfo=UTC),
        config_snapshot=snapshot,
        stage_report=StageReport(),
    ).model_dump(mode="json")
    historical_run_value["config_snapshot"] = {
        "graph_max_content_nodes": 80,
        "graph_recent_days": 90,
        "minimum_final_score": CONFIG.settings.minimum_final_score,
        "minimum_metadata_relevance_score": CONFIG.settings.minimum_metadata_relevance_score,
        "target_item_bytes": CONFIG.settings.storage.target_item_bytes,
        "max_item_bytes": CONFIG.settings.storage.max_item_bytes,
        "max_blog_excerpt_chars": CONFIG.settings.storage.max_blog_excerpt_chars,
        "warn_repository_data_mb": CONFIG.settings.storage.warn_repository_data_mb,
        "warn_pages_artifact_mb": CONFIG.settings.storage.warn_pages_artifact_mb,
        "fail_pages_artifact_mb": CONFIG.settings.storage.fail_pages_artifact_mb,
    }
    for path, value in (
        (historical_item, historical_item_value),
        (historical_digest, {"date": "2025-01-02", "papers": [{"item_id": "historical-paper", "recommendation_reason_zh": "历史推荐", "rank": 1}], "blogs": []}),
        (historical_run, historical_run_value),
        (data / "state.json", State(recommended_item_ids=["historical-paper"]).model_dump(mode="json")),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    current_ids = {f"paper-{index}" for index in range(10)} | {f"blog-{index}" for index in range(10)}
    _write_similarity(stages.similarity, "run-1", current_ids | {"historical-paper"})

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
    copied_historical_report = json.loads((pending / "runs/2025/01/historical-run.json").read_text(encoding="utf-8"))
    assert copied_historical_report["config_snapshot"]["graph_max_content_nodes"] == 80
    assert "graph_initial_content_nodes" not in copied_historical_report["config_snapshot"]
    state = json.loads((pending / "state.json").read_text(encoding="utf-8"))
    assert state["recommended_item_ids"][:2] == ["historical-paper", "paper-0"]
    report_path = next((pending / "runs").rglob("run-1.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["config_snapshot"]["graph_initial_content_nodes"] == CONFIG.settings.graph_initial_content_nodes
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


def test_integrate_strips_stage_one_blog_excerpt_from_canonical_item(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    stage_path = stages.stage1 / "blogs.jsonl"
    values = [json.loads(line) for line in stage_path.read_text(encoding="utf-8").splitlines()]
    next(item for item in values if item["id"] == "blog-0")["excerpt"] = "Transient feed excerpt"
    stage_path.write_text("\n".join(json.dumps(item) for item in values) + "\n", encoding="utf-8")

    bundle = integrate(stages, tmp_path / "bundle", CONFIG)

    canonical = json.loads(
        (bundle.path / "pending-data/items/blogs/2026/08/blog-0.json").read_text(encoding="utf-8")
    )
    assert "excerpt" not in canonical


def test_integrate_enforces_configured_stage_one_blog_excerpt_limit(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path)
    stage_path = stages.stage1 / "blogs.jsonl"
    values = [json.loads(line) for line in stage_path.read_text(encoding="utf-8").splitlines()]
    next(item for item in values if item["kind"] == "blog")["excerpt"] = "12345678"
    stage_path.write_text("\n".join(json.dumps(item) for item in values) + "\n", encoding="utf-8")
    storage = CONFIG.settings.storage.model_copy(update={"max_blog_excerpt_chars": 7})
    settings = CONFIG.settings.model_copy(update={"storage": storage})
    config = CONFIG.model_copy(update={"settings": settings})

    with pytest.raises(ValueError, match="max_blog_excerpt_chars"):
        integrate(stages, tmp_path / "bundle", config)


def test_integrate_rejects_historical_canonical_blog_excerpt(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    stages = fixture_stages(run_root)
    historical = tmp_path / "data/items/blogs/2025/01/historical-blog.json"
    historical.parent.mkdir(parents=True)
    value = _blog("historical-blog", 0.5)
    value["published_at"] = "2025-01-02T00:00:00Z"
    value["excerpt"] = "Legacy canonical excerpt"
    historical.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="historical canonical"):
        integrate(stages, tmp_path / "bundle", CONFIG, repository_data=tmp_path / "data")

    assert not (tmp_path / "bundle").exists()


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
