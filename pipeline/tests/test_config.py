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
        "targets": [topic("content")], "scenarios": [topic("text_feed")],
        "tasks": [topic("ranking")], "methods": [topic("two_tower")],
    })
    _write_yaml(root / "config/sources.yaml", {
        "academic": [{"id": "arxiv", "kind": "arxiv", "enabled": True, "required": True, "weight": 1.0}],
        "blogs": [],
    })
    _write_yaml(root / "config/models.yaml", {"models": {
        "text": {"active_profile": "nvidia_super", "profiles": {"nvidia_super": {
            "base_url_env": "NVIDIA_BASE_URL", "api_key_env": "NVIDIA_API_KEY", "model": "nvidia/super", "context_window_tokens": 1_000_000,
        }}, "reserved_prompt_tokens": 8_000, "reserved_output_tokens": 16_000, "batch_size": 8},
        "vision": {"profile": "nvidia_omni", "invoke_url_env": "NVIDIA_VLM_INVOKE_URL", "api_key_env": "NVIDIA_API_KEY", "model": "nvidia/omni", "context_window_tokens": 262_144, "max_requests_per_paper": 1, "include_all_detected_key_pages": True, "request_defaults": {"max_tokens": 65_536, "reasoning_budget": 16_384, "stream": False, "temperature": 0.6, "top_p": 0.95}},
        "common": {"concurrency_per_worker": 1, "timeout_seconds": 600, "retries": 3},
    }})
    _write_yaml(root / "config/settings.yaml", {
        "daily_target": 8,
        "structured_analysis_min_success_rate": .90,
        "metadata_weights": {"topic_relevance": .30, "scenario_relevance": .25, "source_quality": .15, "novelty": .15, "practical_value": .10, "recency": .05},
        "final_weights": {"metadata_score": .55, "evidence_quality": .20, "business_transferability": .15, "technical_depth": .10},
        "limits": {"http_concurrency": 2, "nvidia_hard_rpm": 40, "nvidia_target_rpm": 30, "nvidia_parallel_workers": 2, "nvidia_concurrency_per_worker": 1, "nvidia_min_interval_seconds_per_worker": 4, "rss_requests_per_run_per_source": 1, "arxiv_min_interval_seconds": 3, "request_timeout_seconds": 45, "retry_attempts": 3, "max_papers_per_run": 100, "max_blogs_per_run": 50, "deep_reading_candidates_per_type": 16, "max_pdf_downloads_per_run": 16, "max_blog_fulltext_fetches_per_run": 16, "pdf_download_concurrency": 1, "blog_download_concurrency_per_domain": 1, "blog_min_interval_seconds_per_domain": 2, "max_pdf_bytes": 20_971_520, "max_pdf_pages": 80, "max_blog_html_bytes": 5_242_880},
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
    assert config.models.text.active_profile == "nvidia_super"
    assert config.settings.limits.nvidia_target_rpm == 30


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
