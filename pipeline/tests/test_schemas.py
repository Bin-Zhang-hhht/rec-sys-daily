from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from recsys_daily.config import TopicEntry, TopicTaxonomy
from recsys_daily.schemas import (
    BlogItem,
    BuildConfigSnapshot,
    ContentItem,
    LLMMetadata,
    Manifest,
    PaperItem,
    RunReport,
    SourceState,
    StageReport,
    State,
    blog_reading_json_schema,
    paper_reading_json_schema,
)


def _taxonomy() -> TopicTaxonomy:
    entry = lambda identifier: TopicEntry(id=identifier, name_zh=identifier, name_en=identifier, terms=[identifier])
    return TopicTaxonomy(targets=[entry("content")], scenarios=[entry("text_feed")], tasks=[entry("ranking")], methods=[entry("two_tower")])


def _item(kind: str = "paper") -> dict[str, object]:
    reading: dict[str, object] = {"analysis_basis": "pdf_text", "visual_analysis": {"status": "not_required"}}
    if kind == "blog":
        reading = {"analysis_basis": "article_html", "system_context_zh": "context"}
    return {"kind": kind, "id": f"{kind}:example", "title": "Original Title", "summary_zh": "summary", "source": "arxiv", "url": "https://example.com", "published_at": "2026-08-09T00:00:00Z", "authors": ["Author"], "targets": ["content"], "scenarios": ["text_feed"], "tasks": ["ranking"], "methods": ["two_tower"], "deep_reading": reading}


def test_content_item_uses_kind_discriminator() -> None:
    adapter = TypeAdapter(ContentItem)
    assert isinstance(adapter.validate_python(_item(), context={"taxonomy": _taxonomy()}), PaperItem)
    assert isinstance(adapter.validate_python(_item("blog"), context={"taxonomy": _taxonomy()}), BlogItem)


def test_unknown_taxonomy_reference_is_rejected() -> None:
    data = _item()
    data["methods"] = ["unknown"]
    with pytest.raises(ValidationError, match="unknown methods id"):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_artifacts_forbid_unknown_fields() -> None:
    data = _item()
    data["source_full_text"] = "not canonical"
    with pytest.raises(ValidationError, match="source_full_text"):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_taxonomy_context_is_required() -> None:
    with pytest.raises(ValidationError, match="taxonomy context is required"):
        PaperItem.model_validate(_item())


def test_legacy_deep_reading_alias_is_rejected() -> None:
    data = _item()
    data["paper_reading"] = data.pop("deep_reading")
    with pytest.raises(ValidationError, match="deep_reading"):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_legacy_visual_fields_are_rejected() -> None:
    data = _item()
    data["deep_reading"]["visual_status"] = "not_required"  # type: ignore[index]
    with pytest.raises(ValidationError, match="visual_status"):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_completed_visual_analysis_requires_provenance_and_finding() -> None:
    data = _item()
    data["deep_reading"]["visual_analysis"] = {"status": "completed", "profile": "vision", "model": "model", "pages": [1]}  # type: ignore[index]
    with pytest.raises(ValidationError, match="visual finding"):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_deep_reading_response_schemas_are_strict_and_evidence_bearing() -> None:
    paper = paper_reading_json_schema()
    blog = blog_reading_json_schema()
    assert paper["additionalProperties"] is False
    assert blog["additionalProperties"] is False
    assert paper["properties"]["analysis_basis"]["enum"] == ["arxiv_html", "pdf_text", "abstract_fallback"]
    assert blog["properties"]["analysis_basis"]["enum"] == ["rss_full_content", "article_html", "excerpt_fallback"]
    assert "problem_zh" in paper["required"]
    assert "system_context_zh" in blog["required"]
    assert paper["properties"]["evidence_refs"]["minItems"] >= 1
    assert blog["properties"]["evidence_refs"]["minItems"] >= 1


def test_manifest_serialization_is_stage_minimal() -> None:
    assert set(Manifest(run_id="run", schema_version="1").model_dump()) == {"run_id", "schema_version"}


def test_generated_timestamps_are_utc() -> None:
    assert State().updated_at.tzinfo is UTC


def test_run_report_carries_config_and_stage_snapshots() -> None:
    snapshot = BuildConfigSnapshot(
        graph_max_content_nodes=80,
        graph_recent_days=90,
        target_item_bytes=16_384,
        max_item_bytes=32_768,
        max_blog_excerpt_chars=4_000,
        warn_repository_data_mb=500,
        warn_pages_artifact_mb=500,
        fail_pages_artifact_mb=900,
    )
    report = RunReport(
        run_id="run",
        started_at=datetime(2026, 8, 10, tzinfo=UTC),
        config_snapshot=snapshot,
        stage_report=StageReport(),
    )
    assert report.config_snapshot.graph_recent_days == 90
    assert report.stage_report.metadata_llm_calls == 0


@pytest.mark.parametrize("timestamp", [datetime(2026, 8, 10), datetime(2026, 8, 10, tzinfo=timezone(timedelta(hours=8)))])
def test_artifact_timestamps_require_utc(timestamp: datetime) -> None:
    taxonomy = {"taxonomy": _taxonomy()}
    snapshot = BuildConfigSnapshot(
        graph_max_content_nodes=80,
        graph_recent_days=90,
        target_item_bytes=16_384,
        max_item_bytes=32_768,
        max_blog_excerpt_chars=4_000,
        warn_repository_data_mb=500,
        warn_pages_artifact_mb=500,
        fail_pages_artifact_mb=900,
    )
    stage_report = StageReport()
    with pytest.raises(ValidationError, match="UTC"):
        LLMMetadata(profile="profile", model="model", generated_at=timestamp)
    with pytest.raises(ValidationError, match="UTC"):
        PaperItem.model_validate(_item() | {"published_at": timestamp}, context=taxonomy)
    with pytest.raises(ValidationError, match="UTC"):
        RunReport(run_id="run", started_at=timestamp, completed_at=timestamp, config_snapshot=snapshot, stage_report=stage_report)
    with pytest.raises(ValidationError, match="UTC"):
        SourceState(last_success_at=timestamp)
    with pytest.raises(ValidationError, match="UTC"):
        State(last_success_at=timestamp, updated_at=timestamp)
