"""Strict canonical and cross-stage artifact schemas."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .config import TopicTaxonomy


def utc_now() -> datetime:
    return datetime.now(UTC)


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphRelation(ArtifactModel):
    type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)
    generated_by: str = Field(min_length=1)


class LLMMetadata(ArtifactModel):
    profile: str = Field(min_length=1)
    model: str = Field(min_length=1)
    generated_at: datetime = Field(default_factory=utc_now)
    degraded: bool = False


class PaperEvidenceRef(ArtifactModel):
    section: str = Field(min_length=1)
    page: int = Field(ge=1)


class BlogEvidenceRef(ArtifactModel):
    heading: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def has_location(self) -> "BlogEvidenceRef":
        if not self.heading and not self.section:
            raise ValueError("blog evidence requires a heading or section")
        return self


class VisualAnalysis(ArtifactModel):
    status: Literal["completed", "not_required", "unavailable"]
    profile: str | None = None
    model: str | None = None
    pages: list[int] = Field(default_factory=list)
    architecture_zh: str | None = None
    table_findings_zh: list[str] = Field(default_factory=list)
    chart_findings_zh: list[str] = Field(default_factory=list)
    limitations_zh: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def completed_has_evidence(self) -> "VisualAnalysis":
        if self.status != "completed":
            return self
        if not self.profile or not self.model or not self.pages:
            raise ValueError("completed visual analysis requires profile, model, and pages")
        findings = [self.architecture_zh, *self.table_findings_zh, *self.chart_findings_zh, *self.limitations_zh]
        if not any(finding and finding.strip() for finding in findings):
            raise ValueError("completed visual analysis requires a visual finding")
        return self


class PaperExperiments(ArtifactModel):
    datasets: list[str] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    findings_zh: list[str] = Field(default_factory=list)


class PaperReading(ArtifactModel):
    analysis_basis: Literal["arxiv_html", "pdf_text", "abstract_fallback"]
    visual_analysis: VisualAnalysis
    problem_zh: str | None = None
    contributions_zh: list[str] = Field(default_factory=list)
    method_zh: str | None = None
    experiments: PaperExperiments = Field(default_factory=PaperExperiments)
    limitations_zh: list[str] = Field(default_factory=list)
    business_implications_zh: list[str] = Field(default_factory=list)
    evidence_refs: list[PaperEvidenceRef] = Field(default_factory=list)


class BlogReading(ArtifactModel):
    analysis_basis: Literal["rss_full_content", "article_html", "excerpt_fallback"]
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


class ItemBase(ArtifactModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    source: str = Field(min_length=1)
    url: str = Field(pattern=r"^https?://")
    published_at: datetime
    authors: list[str]
    targets: list[str]
    scenarios: list[str]
    tasks: list[str]
    methods: list[str]
    relevance_score: float = Field(default=0, ge=0, le=1)
    content_fingerprint: str | None = None
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
    deep_reading: PaperReading


class BlogItem(ItemBase):
    kind: Literal["blog"]
    deep_reading: BlogReading
    excerpt: str | None = Field(default=None, max_length=4_000)


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


class RunReport(ArtifactModel):
    run_id: str = Field(min_length=1)
    started_at: datetime
    completed_at: datetime | None = None
    sources: list[SourceRunStatus] = Field(default_factory=list)
    paper_candidates: int = Field(default=0, ge=0)
    blog_candidates: int = Field(default=0, ge=0)
    paper_recommendations: int = Field(default=0, ge=0)
    blog_recommendations: int = Field(default=0, ge=0)
    llm_calls: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class SourceState(ArtifactModel):
    cursor: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    last_success_at: datetime | None = None


class State(ArtifactModel):
    schema_version: str = "1"
    last_success_at: datetime | None = None
    sources: dict[str, SourceState] = Field(default_factory=dict)
    recommended_item_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class Manifest(ArtifactModel):
    run_id: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    _created_at: datetime = PrivateAttr(default_factory=utc_now)

    @property
    def created_at(self) -> datetime:
        return self._created_at
