from datetime import UTC

import pytest
from pydantic import TypeAdapter, ValidationError

from recsys_daily.config import TopicEntry, TopicTaxonomy
from recsys_daily.schemas import BlogItem, ContentItem, Manifest, PaperItem, State


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


def test_manifest_serialization_is_stage_minimal() -> None:
    assert set(Manifest(run_id="run", schema_version="1").model_dump()) == {"run_id", "schema_version"}


def test_generated_timestamps_are_utc() -> None:
    assert State().updated_at.tzinfo is UTC
