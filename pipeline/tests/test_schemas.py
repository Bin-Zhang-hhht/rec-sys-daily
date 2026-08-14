from datetime import UTC, datetime, timedelta, timezone

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import TypeAdapter, ValidationError

from recsys_daily.config import TopicEntry, TopicTaxonomy
from recsys_daily.schemas import (
    BlogItem,
    BlogReading,
    BuildConfigSnapshot,
    ContentItem,
    LLMMetadata,
    Manifest,
    PaperItem,
    PaperReading,
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
    reading: dict[str, object] = {"analysis_basis": "mineru_full_text"}
    if kind == "blog":
        reading = {"analysis_basis": "article_html", "system_context_zh": "context"}
    item = {"kind": kind, "id": f"{kind}:example", "title": "Original Title", "summary_zh": "summary", "source": "arxiv", "url": "https://example.com", "published_at": "2026-08-09T00:00:00Z", "authors": ["Author"], "targets": ["content"], "scenarios": ["text_feed"], "tasks": ["ranking"], "methods": ["two_tower"], "deep_reading": reading}
    if kind == "paper":
        item.update({"abstract": "Original abstract", "arxiv_id": "2608.00001", "doi": None})
    return item


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


@pytest.mark.parametrize("field", ["visual_status", "visual_analysis"])
def test_legacy_visual_fields_are_rejected(field: str) -> None:
    data = _item()
    data["deep_reading"][field] = {"status": "not_required"}  # type: ignore[index]
    with pytest.raises(ValidationError, match=field):
        PaperItem.model_validate(data, context={"taxonomy": _taxonomy()})


def test_deep_reading_response_schemas_are_strict_and_evidence_bearing() -> None:
    paper = paper_reading_json_schema()
    blog = blog_reading_json_schema()
    assert paper["additionalProperties"] is False
    assert blog["additionalProperties"] is False
    assert paper["properties"]["analysis_basis"]["enum"] == ["mineru_full_text", "abstract_fallback"]
    assert blog["properties"]["analysis_basis"]["enum"] == ["rss_full_content", "article_html", "excerpt_fallback"]
    assert "problem_zh" in paper["required"]
    assert "system_context_zh" in blog["required"]
    assert "evidence_refs" in paper["required"]
    assert "evidence_refs" in blog["required"]
    assert "visual_analysis" not in paper["properties"]
    assert paper["properties"]["method_zh"]["type"] == ["string", "null"]
    assert blog["properties"]["architecture_zh"]["type"] == ["string", "null"]


@pytest.mark.parametrize("schema", [paper_reading_json_schema(), blog_reading_json_schema()])
def test_deep_reading_schemas_are_recursively_provider_strict(schema: dict[str, object]) -> None:
    def assert_strict(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                assert value.get("additionalProperties") is False
                assert set(value.get("required", [])) == set(value.get("properties", {}))
            for nested in value.values():
                assert_strict(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_strict(nested)

    assert_strict(schema)


def test_paper_response_schema_accepts_method_and_limitation_only() -> None:
    schema = paper_reading_json_schema()
    payload = {
        "analysis_basis": "abstract_fallback",
        "evidence_quality": None,
        "business_transferability": None,
        "technical_depth": None,
        "problem_zh": "A bounded retrieval problem.",
        "contributions_zh": [],
        "method_zh": "A two-tower method.",
        "experiments": {"datasets": [], "baselines": [], "metrics": [], "findings_zh": []},
        "limitations_zh": ["Evaluation is limited to one dataset."],
        "business_implications_zh": [],
        "evidence_refs": [],
    }

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def test_blog_response_schema_accepts_evidence_only_analysis() -> None:
    schema = blog_reading_json_schema()
    payload = {
        "analysis_basis": "excerpt_fallback",
        "evidence_quality": None,
        "business_transferability": None,
        "technical_depth": None,
        "system_context_zh": "A feed-ranking service.",
        "architecture_zh": None,
        "implementation_zh": None,
        "production_constraints_zh": [],
        "tradeoffs_zh": [],
        "results_zh": [],
        "lessons_zh": [],
        "limitations_zh": [],
        "business_implications_zh": [],
        "evidence_refs": [{"heading": "Architecture", "section": None}],
    }

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def test_blog_evidence_requires_one_nonempty_location() -> None:
    evidence_schema = blog_reading_json_schema()["properties"]["evidence_refs"]["items"]
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(evidence_schema).validate({"heading": None, "section": None})
    with pytest.raises(ValidationError, match="heading or section"):
        BlogReading(
            analysis_basis="excerpt_fallback",
            system_context_zh="A feed-ranking service.",
            evidence_refs=[{"heading": " ", "section": None}],
        )


def test_post_schema_quality_validation_rejects_empty_semantic_alternatives() -> None:
    paper = PaperReading(
        analysis_basis="abstract_fallback",
        problem_zh="A bounded retrieval problem.",
    )
    blog = BlogReading(analysis_basis="excerpt_fallback", system_context_zh="A feed-ranking service.")

    from recsys_daily.deep_read import validate_reading_quality

    with pytest.raises(ValueError, match="method or contribution"):
        validate_reading_quality(paper)
    with pytest.raises(ValueError, match="architecture, implementation, lesson, or evidence"):
        validate_reading_quality(blog)


def test_manifest_serialization_is_stage_minimal() -> None:
    assert set(Manifest(run_id="run", schema_version="1").model_dump()) == {"run_id", "schema_version"}


def test_manifest_rejects_non_v1_schema_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        Manifest(run_id="run", schema_version="2")


def test_generated_timestamps_are_utc() -> None:
    assert State().updated_at.tzinfo is UTC


def test_run_report_carries_config_and_stage_snapshots() -> None:
    snapshot = BuildConfigSnapshot(
        graph_max_content_nodes=80,
        graph_recent_days=90,
        minimum_final_score=0.5,
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
        minimum_final_score=0.5,
        target_item_bytes=16_384,
        max_item_bytes=32_768,
        max_blog_excerpt_chars=4_000,
        warn_repository_data_mb=500,
        warn_pages_artifact_mb=500,
        fail_pages_artifact_mb=900,
    )
    stage_report = StageReport()
    with pytest.raises(ValidationError, match="UTC"):
        LLMMetadata(model="model", generated_at=timestamp)
    with pytest.raises(ValidationError, match="UTC"):
        PaperItem.model_validate(_item() | {"published_at": timestamp}, context=taxonomy)
    with pytest.raises(ValidationError, match="UTC"):
        RunReport(run_id="run", started_at=timestamp, completed_at=timestamp, config_snapshot=snapshot, stage_report=stage_report)
    with pytest.raises(ValidationError, match="UTC"):
        SourceState(last_success_at=timestamp)
    with pytest.raises(ValidationError, match="UTC"):
        State(last_success_at=timestamp, updated_at=timestamp)


def test_llm_metadata_rejects_removed_profile_field() -> None:
    with pytest.raises(ValidationError, match="profile"):
        LLMMetadata(profile="deepseek", model="deepseek-v4-flash")
