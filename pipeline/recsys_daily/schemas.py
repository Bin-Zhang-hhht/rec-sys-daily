"""Strict canonical and cross-stage artifact schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, PositiveInt, PrivateAttr, model_validator

from .config import TopicTaxonomy


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_utc(timestamp: datetime) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise ValueError("timestamp must be timezone-aware UTC")
    return timestamp


UtcDatetime = Annotated[datetime, AfterValidator(require_utc)]


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphRelation(ArtifactModel):
    type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)


class LLMMetadata(ArtifactModel):
    model: str = Field(min_length=1)
    generated_at: UtcDatetime = Field(default_factory=utc_now)
    degraded: bool = False


class PaperEvidenceRef(ArtifactModel):
    section: str = Field(min_length=1)
    page: int = Field(ge=1)


class BlogEvidenceRef(ArtifactModel):
    heading: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def has_location(self) -> "BlogEvidenceRef":
        if not any(value and value.strip() for value in (self.heading, self.section)):
            raise ValueError("blog evidence requires a heading or section")
        return self


class PaperExperiments(ArtifactModel):
    datasets: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    findings_zh: list[str] = Field(default_factory=list)


class PaperReading(ArtifactModel):
    analysis_basis: Literal["mineru_full_text", "abstract_fallback"]
    evidence_quality: float | None = Field(default=None, ge=0, le=1)
    business_transferability: float | None = Field(default=None, ge=0, le=1)
    technical_depth: float | None = Field(default=None, ge=0, le=1)
    problem_zh: str | None = None
    contributions_zh: list[str] = Field(default_factory=list)
    method_zh: str | None = None
    experiments: PaperExperiments = Field(default_factory=PaperExperiments)
    limitations_zh: list[str] = Field(default_factory=list)
    business_implications_zh: list[str] = Field(default_factory=list)
    evidence_refs: list[PaperEvidenceRef] = Field(default_factory=list)


class BlogReading(ArtifactModel):
    analysis_basis: Literal["rss_full_content", "article_html", "excerpt_fallback"]
    evidence_quality: float | None = Field(default=None, ge=0, le=1)
    business_transferability: float | None = Field(default=None, ge=0, le=1)
    technical_depth: float | None = Field(default=None, ge=0, le=1)
    system_context_zh: str | None = None
    architecture_zh: str | None = None
    implementation_zh: str | None = None
    production_constraints_zh: list[str] = Field(default_factory=list)
    tradeoffs_zh: list[str] = Field(default_factory=list)
    results_zh: list[str] = Field(default_factory=list)
    lessons_zh: list[str] = Field(default_factory=list)
    limitations_zh: list[str] = Field(default_factory=list)
    business_implications_zh: list[str] = Field(default_factory=list)
    evidence_refs: list[BlogEvidenceRef] = Field(default_factory=list)


def _paper_evidence_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "section": {"type": "string", "minLength": 1},
                "page": {"type": "integer", "minimum": 1},
            },
            "required": ["section", "page"],
        },
    }


def _blog_evidence_schema() -> dict[str, Any]:
    nullable_locations = {
        "heading": {"type": ["string", "null"], "minLength": 1},
        "section": {"type": ["string", "null"], "minLength": 1},
    }

    def location_branch(nonempty: str) -> dict[str, Any]:
        properties = dict(nullable_locations)
        properties[nonempty] = {"type": "string", "minLength": 1}
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(properties),
        }

    return {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": nullable_locations,
            "required": ["heading", "section"],
            "anyOf": [location_branch("heading"), location_branch("section")],
        },
    }


def paper_reading_json_schema() -> dict[str, Any]:
    """Strict response contract for paper deep-reading model calls."""
    string_list = {"type": "array", "items": {"type": "string", "minLength": 1}}
    experiments = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "datasets": string_list,
            "baselines": string_list,
            "metrics": string_list,
            "findings_zh": string_list,
        },
        "required": ["datasets", "baselines", "metrics", "findings_zh"],
    }
    properties: dict[str, Any] = {
        "analysis_basis": {"type": "string", "enum": ["mineru_full_text", "abstract_fallback"]},
        "evidence_quality": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "business_transferability": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "technical_depth": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "problem_zh": {"type": "string", "minLength": 1},
        "contributions_zh": string_list,
        "method_zh": {"type": ["string", "null"], "minLength": 1},
        "experiments": experiments,
        "limitations_zh": string_list,
        "business_implications_zh": string_list,
        "evidence_refs": _paper_evidence_schema(),
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def blog_reading_json_schema() -> dict[str, Any]:
    """Strict response contract for blog deep-reading model calls."""
    string_list = {"type": "array", "items": {"type": "string", "minLength": 1}}
    properties: dict[str, Any] = {
        "analysis_basis": {"type": "string", "enum": ["rss_full_content", "article_html", "excerpt_fallback"]},
        "evidence_quality": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "business_transferability": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "technical_depth": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "system_context_zh": {"type": "string", "minLength": 1},
        "architecture_zh": {"type": ["string", "null"], "minLength": 1},
        "implementation_zh": {"type": ["string", "null"], "minLength": 1},
        "production_constraints_zh": string_list,
        "tradeoffs_zh": string_list,
        "results_zh": string_list,
        "lessons_zh": string_list,
        "limitations_zh": string_list,
        "business_implications_zh": string_list,
        "evidence_refs": _blog_evidence_schema(),
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


class Stage1Metadata(ArtifactModel):
    """Transient metadata produced for a bounded Stage 1 candidate batch."""

    id: str = Field(min_length=1)
    summary_zh: str | None = None
    targets: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)
    tasks: list[str] = Field(default_factory=list)
    methods: list[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0, ge=0, le=1)
    graph_relations: list[GraphRelation] = Field(default_factory=list)
    degraded: bool = False


class ItemBase(ArtifactModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    published_at: UtcDatetime
    authors: list[str]
    targets: list[str]
    scenarios: list[str]
    tasks: list[str]
    methods: list[str]
    relevance_score: float = Field(default=0, ge=0, le=1)
    final_score: float = Field(default=0, ge=0, le=1)
    graph_relations: list[GraphRelation] = Field(default_factory=list)
    llm: LLMMetadata | None = None

    @model_validator(mode="wrap")
    @classmethod
    def validate_taxonomy(cls, value: Any, handler: Any, info: Any) -> Any:
        item = handler(value)
        taxonomy = info.context.get("taxonomy") if info.context else None
        if not isinstance(taxonomy, TopicTaxonomy):
            raise ValueError("taxonomy context is required")
        for category in ("targets", "scenarios", "tasks", "methods"):
            allowed = {entry.id for entry in getattr(taxonomy, category)}
            unknown = next((entry_id for entry_id in getattr(item, category) if entry_id not in allowed), None)
            if unknown is not None:
                raise ValueError(f"unknown {category} id: {unknown}")
        return item


class PaperItem(ItemBase):
    kind: Literal["paper"]
    abstract: str = Field(min_length=1)
    arxiv_id: str = Field(min_length=1)
    doi: str | None = None
    deep_reading: PaperReading


class BlogItem(ItemBase):
    kind: Literal["blog"]
    deep_reading: BlogReading


ContentItem = Annotated[Union[PaperItem, BlogItem], Field(discriminator="kind")]


class DigestEntry(ArtifactModel):
    item_id: str = Field(min_length=1)
    recommendation_reason_zh: str = Field(min_length=1)
    rank: int = Field(ge=1)


class Digest(ArtifactModel):
    date: date
    papers: list[DigestEntry] = Field(default_factory=list)
    blogs: list[DigestEntry] = Field(default_factory=list)


class SourceRunStatus(ArtifactModel):
    source_id: str = Field(min_length=1)
    success: bool
    warning: str | None = None


class BuildConfigSnapshot(ArtifactModel):
    graph_max_content_nodes: int = Field(ge=1)
    graph_recent_days: PositiveInt
    minimum_final_score: float = Field(ge=0, le=1)
    minimum_metadata_relevance_score: float = Field(ge=0, le=1)
    target_item_bytes: PositiveInt
    max_item_bytes: PositiveInt
    max_blog_excerpt_chars: PositiveInt
    warn_repository_data_mb: PositiveInt
    warn_pages_artifact_mb: PositiveInt
    fail_pages_artifact_mb: PositiveInt


class StageReport(ArtifactModel):
    sources: list[SourceRunStatus] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    collected_paper_candidates: int = Field(default=0, ge=0)
    collected_blog_candidates: int = Field(default=0, ge=0)
    prefilter_paper_candidates: int = Field(default=0, ge=0)
    prefilter_blog_candidates: int = Field(default=0, ge=0)
    shortlist_paper_candidates: int = Field(default=0, ge=0)
    shortlist_blog_candidates: int = Field(default=0, ge=0)
    metadata_llm_calls: int = Field(default=0, ge=0)
    metadata_llm_success_rate: float = Field(default=1.0, ge=0, le=1)
    metadata_degraded_count: int = Field(default=0, ge=0)
    metadata_label_rejections: int = Field(default=0, ge=0)
    metadata_relevance_rejections: int = Field(default=0, ge=0)


class RunReport(ArtifactModel):
    run_id: str = Field(min_length=1)
    started_at: UtcDatetime
    config_snapshot: BuildConfigSnapshot
    stage_report: StageReport
    completed_at: UtcDatetime | None = None
    sources: list[SourceRunStatus] = Field(default_factory=list)
    paper_candidates: int = Field(default=0, ge=0)
    blog_candidates: int = Field(default=0, ge=0)
    paper_recommendations: int = Field(default=0, ge=0)
    blog_recommendations: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    structured_analysis_success_rate: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)


class SourceState(ArtifactModel):
    cursor: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_success_at: UtcDatetime | None = None


class State(ArtifactModel):
    schema_version: Literal["1"] = "1"
    last_success_at: UtcDatetime | None = None
    sources: dict[str, SourceState] = Field(default_factory=dict)
    recommended_item_ids: list[str] = Field(default_factory=list)
    updated_at: UtcDatetime = Field(default_factory=utc_now)


class Manifest(ArtifactModel):
    run_id: str = Field(min_length=1)
    schema_version: Literal["1"]
    _created_at: datetime = PrivateAttr(default_factory=utc_now)

    @property
    def created_at(self) -> datetime:
        return self._created_at
