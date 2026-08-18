from pathlib import Path

import pytest
import yaml

from recsys_daily.config import load_config


def _write_yaml(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_config(root: Path) -> None:
    topic = lambda identifier: {"id": identifier, "name_zh": identifier, "name_en": identifier, "terms": [identifier]}
    _write_yaml(root / "config/topics.yaml", {
        "collection_terms": ["recommendation"],
        "targets": [topic("content")], "scenarios": [topic("text_feed")],
        "tasks": [topic("ranking")], "methods": [topic("two_tower")],
    })
    _write_yaml(root / "config/sources.yaml", {
        "academic": [{"id": "arxiv", "kind": "arxiv", "enabled": True, "required": True, "weight": 1.0}],
        "blogs": [],
    })
    _write_yaml(root / "config/models.yaml", {"models": {
        "text": {
            "base_url_env": "DEEPSEEK_BASE_URL", "api_key_env": "DEEPSEEK_API_KEY",
            "model": "deepseek-v4-flash", "context_window_tokens": 1_000_000,
            "reserved_prompt_tokens": 8_000, "reserved_output_tokens": 16_000, "batch_size": 8,
        },
        "mineru": {"api_key_env": "MINERU_API_KEY", "base_url": "https://mineru.net/api/v4", "model_version": "vlm", "upload_timeout_seconds": 120, "poll_timeout_seconds": 900, "poll_interval_seconds": 5, "max_pdf_bytes": 20_971_520, "max_pdf_pages": 200},
        "common": {"timeout_seconds": 600, "retries": 3},
    }})
    _write_yaml(root / "config/settings.yaml", {
        "daily_target": 10,
        "minimum_final_score": 0.5,
        "minimum_metadata_relevance_score": 0.65,
        "request_user_agent": "RecSysDaily/1.0",
        "structured_analysis_min_success_rate": .80,
        "metadata_weights": {"topic_relevance": .30, "scenario_relevance": .25, "source_quality": .15, "novelty": .15, "practical_value": .10, "recency": .05},
        "final_weights": {"metadata_score": .55, "evidence_quality": .20, "business_transferability": .15, "technical_depth": .10},
        "limits": {"http_concurrency": 2, "arxiv_min_interval_seconds": 3, "request_timeout_seconds": 45, "retry_attempts": 3, "retry_backoff_seconds": 1, "retry_max_delay_seconds": 30, "max_papers_per_run": 100, "max_blogs_per_run": 50, "deep_reading_candidates_per_type": 20, "pdf_download_concurrency": 1, "blog_download_concurrency_per_domain": 1, "blog_min_interval_seconds_per_domain": 2, "max_blog_html_bytes": 5_242_880},
        "graph_max_content_nodes": 80,
        "graph_recent_days": 90,
        "storage": {"target_item_bytes": 16_384, "max_item_bytes": 32_768, "max_blog_excerpt_chars": 4_000, "warn_repository_data_mb": 500, "warn_pages_artifact_mb": 500, "fail_pages_artifact_mb": 900},
    })


def test_repository_config_snapshot_is_ordered_and_public() -> None:
    snapshot = load_config(Path(__file__).parents[2]).topics.to_public_snapshot()
    assert snapshot["scenarios"][3]["id"] == "friend_recommendation"
    assert "terms" not in snapshot["targets"][0]


def test_documented_nested_model_and_settings_shapes_load(tmp_path: Path) -> None:
    _write_config(tmp_path)
    config = load_config(tmp_path)
    assert config.models.text.model == "deepseek-v4-flash"
    assert config.settings.minimum_metadata_relevance_score == 0.65
    assert not hasattr(config.settings.limits, "llm_target_rpm")
    assert config.models.mineru.model_version == "vlm"


def test_config_uses_mineru_and_has_no_fetch_attempt_caps() -> None:
    config = load_config(Path(__file__).parents[2])
    limits = config.settings.limits
    assert not hasattr(limits, "rss_requests_per_run_per_source")
    assert not hasattr(limits, "max_pdf_downloads_per_run")
    assert not hasattr(limits, "max_blog_fulltext_fetches_per_run")
    assert config.models.mineru.api_key_env == "MINERU_API_KEY"
    assert config.models.mineru.max_pdf_pages == 200


@pytest.mark.parametrize("legacy_field", [
    "nvidia_hard_rpm",
    "nvidia_target_rpm",
    "llm_hard_rpm",
    "llm_target_rpm",
    "llm_min_interval_seconds_per_worker",
])
def test_legacy_llm_rate_limit_fields_are_rejected(tmp_path: Path, legacy_field: str) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["limits"][legacy_field] = 40
    _write_yaml(path, data)

    with pytest.raises(ValueError, match=legacy_field):
        load_config(tmp_path)


def test_mineru_api_key_reference_must_be_an_environment_name(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/models.yaml"
    data = yaml.safe_load(path.read_text())
    data["models"]["mineru"]["api_key_env"] = "not-a-variable"
    _write_yaml(path, data)

    with pytest.raises(ValueError, match="api_key_env"):
        load_config(tmp_path)


def test_repository_request_user_agent_is_identifiable() -> None:
    config = load_config(Path(__file__).parents[2])
    assert config.settings.request_user_agent == "RecSysDaily/1.0"


def test_repository_source_retry_timing_is_typed_and_bounded() -> None:
    limits = load_config(Path(__file__).parents[2]).settings.limits
    assert limits.retry_backoff_seconds == 1
    assert limits.retry_max_delay_seconds == 30


def test_retry_max_delay_must_cover_initial_backoff(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["limits"]["retry_backoff_seconds"] = 10
    data["limits"]["retry_max_delay_seconds"] = 5
    _write_yaml(path, data)

    with pytest.raises(ValueError, match="retry_max_delay_seconds"):
        load_config(tmp_path)


def test_duplicate_taxonomy_ids_are_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/topics.yaml"
    data = yaml.safe_load(path.read_text())
    data["methods"][0]["id"] = "content"
    _write_yaml(path, data)
    with pytest.raises(ValueError, match="duplicate topic id"):
        load_config(tmp_path)


def test_flattened_settings_are_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/settings.yaml"
    data = yaml.safe_load(path.read_text())
    data["max_papers_per_run"] = data["limits"].pop("max_papers_per_run")
    _write_yaml(path, data)
    with pytest.raises(ValueError, match="max_papers_per_run"):
        load_config(tmp_path)


def test_flattened_model_configuration_is_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/models.yaml"
    data = yaml.safe_load(path.read_text())["models"]
    _write_yaml(path, data)
    with pytest.raises(ValueError, match="models.yaml"):
        load_config(tmp_path)


def test_model_environment_references_must_be_identifiers(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/models.yaml"
    data = yaml.safe_load(path.read_text())
    data["models"]["text"]["base_url_env"] = "https://example.com/v1"
    _write_yaml(path, data)

    with pytest.raises(ValueError, match="base_url_env"):
        load_config(tmp_path)


def test_text_environment_references_match_the_documented_deepseek_names(tmp_path: Path) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/models.yaml"
    data = yaml.safe_load(path.read_text())
    data["models"]["text"]["api_key_env"] = "OTHER_API_KEY"
    _write_yaml(path, data)

    with pytest.raises(ValueError, match="DEEPSEEK"):
        load_config(tmp_path)


@pytest.mark.parametrize("legacy_field", ["profiles", "active_profile", "vision"])
def test_legacy_model_selection_and_vision_fields_are_rejected(tmp_path: Path, legacy_field: str) -> None:
    _write_config(tmp_path)
    path = tmp_path / "config/models.yaml"
    data = yaml.safe_load(path.read_text())
    target = data["models"] if legacy_field == "vision" else data["models"]["text"]
    target[legacy_field] = {}
    _write_yaml(path, data)

    with pytest.raises(ValueError, match=legacy_field):
        load_config(tmp_path)
