"""Typed, side-effect-free application configuration loading."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopicEntry(StrictModel):
    id: str = Field(min_length=1)
    name_zh: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    terms: list[str] = Field(min_length=1)


class TopicTaxonomy(StrictModel):
    targets: list[TopicEntry] = Field(min_length=1)
    scenarios: list[TopicEntry] = Field(min_length=1)
    tasks: list[TopicEntry] = Field(min_length=1)
    methods: list[TopicEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def ids_are_unique(self) -> "TopicTaxonomy":
        ids = [entry.id for category in (self.targets, self.scenarios, self.tasks, self.methods) for entry in category]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate topic id")
        return self

    def to_public_snapshot(self) -> dict[str, list[dict[str, str]]]:
        return {
            name: [{"id": entry.id, "name_zh": entry.name_zh, "name_en": entry.name_en} for entry in entries]
            for name, entries in (("targets", self.targets), ("scenarios", self.scenarios), ("tasks", self.tasks), ("methods", self.methods))
        }


class AcademicSource(StrictModel):
    id: Literal["arxiv"]
    kind: Literal["arxiv"]
    enabled: bool
    required: bool
    weight: PositiveFloat


class BlogSource(StrictModel):
    id: str = Field(min_length=1)
    kind: Literal["rss"]
    name: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    enabled: bool
    required: bool
    weight: PositiveFloat
    scenarios: list[str] = Field(default_factory=list)


class SourcesConfig(StrictModel):
    academic: list[AcademicSource] = Field(min_length=1, max_length=1)
    blogs: list[BlogSource]

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> "SourcesConfig":
        ids = [source.id for source in [*self.academic, *self.blogs]]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source id")
        return self


class TextProfile(StrictModel):
    base_url_env: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    context_window_tokens: PositiveInt

    @field_validator("base_url_env", "api_key_env")
    @classmethod
    def environment_variable_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
            raise ValueError("environment reference must be an environment-variable identifier")
        return value


class TextModels(StrictModel):
    active_profile: str = Field(min_length=1)
    profiles: dict[str, TextProfile] = Field(min_length=1)
    reserved_prompt_tokens: PositiveInt
    reserved_output_tokens: PositiveInt
    batch_size: PositiveInt

    @model_validator(mode="after")
    def active_profile_exists(self) -> "TextModels":
        if self.active_profile not in self.profiles:
            raise ValueError("unknown active text profile")
        expected_references = {
            "nvidia_super": ("NVIDIA_BASE_URL", "NVIDIA_API_KEY"),
            "nvidia_ultra": ("NVIDIA_BASE_URL", "NVIDIA_API_KEY"),
            "deepseek_v4_flash": ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY"),
        }
        for profile_name, expected in expected_references.items():
            profile = self.profiles.get(profile_name)
            if profile and (profile.base_url_env, profile.api_key_env) != expected:
                raise ValueError(f"{profile_name} must use its documented environment references")
        return self

    def active(self) -> TextProfile:
        return self.profiles[self.active_profile]


class VisionRequestDefaults(StrictModel):
    max_tokens: PositiveInt
    reasoning_budget: PositiveInt
    stream: Literal[False]
    temperature: float = Field(ge=0, le=2)
    top_p: float = Field(gt=0, le=1)


class VisionModels(StrictModel):
    profile: str = Field(min_length=1)
    invoke_url_env: str = Field(min_length=1)
    api_key_env: str = Field(min_length=1)
    model: str = Field(min_length=1)
    context_window_tokens: PositiveInt
    max_requests_per_paper: Literal[1]
    include_all_detected_key_pages: Literal[True]
    request_defaults: VisionRequestDefaults

    @field_validator("invoke_url_env", "api_key_env")
    @classmethod
    def environment_variable_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", value):
            raise ValueError("environment reference must be an environment-variable identifier")
        return value

    @model_validator(mode="after")
    def documented_environment_references(self) -> "VisionModels":
        if self.invoke_url_env != "NVIDIA_VLM_INVOKE_URL" or self.api_key_env != "NVIDIA_API_KEY":
            raise ValueError("vision must use NVIDIA_VLM_INVOKE_URL and NVIDIA_API_KEY")
        return self


class ModelCommon(StrictModel):
    concurrency_per_worker: Literal[1]
    timeout_seconds: PositiveInt
    retries: PositiveInt


class ModelConfig(StrictModel):
    text: TextModels
    vision: VisionModels
    common: ModelCommon


class ScoreWeights(StrictModel):
    topic_relevance: float = Field(ge=0)
    scenario_relevance: float = Field(ge=0)
    source_quality: float = Field(ge=0)
    novelty: float = Field(ge=0)
    practical_value: float = Field(ge=0)
    recency: float = Field(ge=0)

    @model_validator(mode="after")
    def total_is_one(self) -> "ScoreWeights":
        if abs(sum(self.model_dump().values()) - 1) > 1e-9:
            raise ValueError("metadata score weights must sum to 1")
        return self


class FinalScoreWeights(StrictModel):
    metadata_score: float = Field(ge=0)
    evidence_quality: float = Field(ge=0)
    business_transferability: float = Field(ge=0)
    technical_depth: float = Field(ge=0)

    @model_validator(mode="after")
    def total_is_one(self) -> "FinalScoreWeights":
        if abs(sum(self.model_dump().values()) - 1) > 1e-9:
            raise ValueError("final score weights must sum to 1")
        return self


class Limits(StrictModel):
    http_concurrency: PositiveInt
    nvidia_hard_rpm: PositiveInt
    nvidia_target_rpm: PositiveInt
    nvidia_parallel_workers: Literal[2]
    nvidia_concurrency_per_worker: Literal[1]
    nvidia_min_interval_seconds_per_worker: Literal[4]
    rss_requests_per_run_per_source: Literal[2]
    arxiv_min_interval_seconds: PositiveInt
    request_timeout_seconds: PositiveInt
    retry_attempts: PositiveInt
    retry_backoff_seconds: PositiveFloat
    retry_max_delay_seconds: PositiveFloat
    max_papers_per_run: Literal[100]
    max_blogs_per_run: Literal[50]
    deep_reading_candidates_per_type: Literal[16]
    max_pdf_downloads_per_run: Literal[16]
    max_blog_fulltext_fetches_per_run: Literal[16]
    pdf_download_concurrency: Literal[1]
    blog_download_concurrency_per_domain: Literal[1]
    blog_min_interval_seconds_per_domain: PositiveInt
    max_pdf_bytes: PositiveInt
    max_pdf_pages: PositiveInt
    max_blog_html_bytes: PositiveInt

    @model_validator(mode="after")
    def nvidia_limit_is_valid(self) -> "Limits":
        if self.nvidia_target_rpm > self.nvidia_hard_rpm:
            raise ValueError("nvidia_target_rpm must not exceed nvidia_hard_rpm")
        if self.retry_backoff_seconds > self.retry_max_delay_seconds:
            raise ValueError("retry_max_delay_seconds must be at least retry_backoff_seconds")
        return self


class StorageSettings(StrictModel):
    target_item_bytes: PositiveInt
    max_item_bytes: PositiveInt
    max_blog_excerpt_chars: PositiveInt
    warn_repository_data_mb: PositiveInt
    warn_pages_artifact_mb: PositiveInt
    fail_pages_artifact_mb: PositiveInt


class Settings(StrictModel):
    daily_target: Literal[8]
    minimum_final_score: float = Field(ge=0, le=1)
    request_user_agent: str = Field(min_length=1)
    structured_analysis_min_success_rate: float = Field(ge=0, le=1)
    metadata_weights: ScoreWeights
    final_weights: FinalScoreWeights
    limits: Limits
    graph_max_content_nodes: PositiveInt
    graph_recent_days: PositiveInt
    storage: StorageSettings


class AppConfig(StrictModel):
    sources: SourcesConfig
    topics: TopicTaxonomy
    models: ModelConfig
    settings: Settings

    @model_validator(mode="after")
    def source_scenarios_exist(self) -> "AppConfig":
        allowed = {entry.id for entry in self.topics.scenarios}
        if any(scenario not in allowed for source in self.sources.blogs for scenario in source.scenarios):
            raise ValueError("unknown source scenario id")
        return self


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"failed to load configuration {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"configuration must be a mapping: {path}")
    return value


def load_config(root: Path) -> AppConfig:
    """Load documented YAML configuration without reading environment variables."""
    config_dir = root / "config"
    models_document = _read_yaml(config_dir / "models.yaml")
    if set(models_document) != {"models"}:
        raise ValueError("models.yaml must contain only the documented models mapping")
    return AppConfig(
        sources=SourcesConfig.model_validate(_read_yaml(config_dir / "sources.yaml")),
        topics=TopicTaxonomy.model_validate(_read_yaml(config_dir / "topics.yaml")),
        models=ModelConfig.model_validate(models_document["models"]),
        settings=Settings.model_validate(_read_yaml(config_dir / "settings.yaml")),
    )
