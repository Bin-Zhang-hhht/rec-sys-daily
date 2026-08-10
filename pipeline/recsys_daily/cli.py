"""The four-stage command line for production and offline fixture runs."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import base64
import json
from pathlib import Path
import shutil
from typing import Any

import typer

from .artifacts import write_json, write_jsonl
from .collect import Candidate, collect_candidates, stable_id
from .config import AppConfig, load_config
from .content import BlogFeedCache, ContentServices, DomainRateLimiter, fetch_article_html as fetch_article_html_request, fetch_bytes as fetch_bytes_request, fetch_text as fetch_text_request
from .deep_read import DeepReadServices, deep_read
from .integrate import StageInputs, integrate
from .llm import TextClient, TokenBudget, VisionClient
from .metadata import MetadataResult, analyze_metadata
from .prompts import json_messages
from .rate_limit import RateLimiter
from .security import fetch_public_url
from .schemas import BlogItem, PaperItem, SourceRunStatus, SourceState, Stage1Metadata, StageReport
from .filtering import prefilter


app = typer.Typer(no_args_is_help=True, add_completion=False)


def _root(root: Path | None = None) -> Path:
    candidate = (root or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "config" / "topics.yaml").exists():
            return path
    raise typer.BadParameter("could not locate config/topics.yaml")


def _candidate_document(candidate: Candidate, metadata: Stage1Metadata | None = None) -> dict[str, Any]:
    value = asdict(candidate)
    # Full RSS content is process-local input and must never cross a stage
    # boundary or enter a retained artifact.
    value.pop("feed_content", None)
    value["id"] = stable_id(candidate)
    value["published_at"] = candidate.published_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    for key in ("authors", "categories", "source_scenarios"):
        value[key] = list(value[key])
    if metadata is not None:
        value.update(metadata.model_dump(mode="json"))
    return value


def _write_stage_one(
    output: Path,
    run_id: str,
    candidates: list[Candidate],
    source_states: dict[str, SourceState] | None = None,
    metadata: MetadataResult | None = None,
    stage_report: StageReport | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "manifest.json", {"run_id": run_id, "schema_version": "1"})
    metadata_by_id = {item.id: item for item in (metadata.items if metadata else [])}
    for kind in ("paper", "blog"):
        values = [_candidate_document(candidate, metadata_by_id.get(stable_id(candidate))) for candidate in candidates if candidate.kind == kind]
        write_jsonl(output / f"{kind}s.jsonl", values)
    write_json(
        output / "source-states.json",
        {source_id: state.model_dump(mode="json") for source_id, state in (source_states or {}).items()},
    )
    write_json(output / "stage-report.json", (stage_report or StageReport()).model_dump(mode="json"))


def _collection_stage_report(config: AppConfig, result: Any, metadata: MetadataResult) -> StageReport:
    warnings = list(result.warnings)
    sources: list[SourceRunStatus] = []
    for source in [*config.sources.academic, *config.sources.blogs]:
        warning = next((item for item in warnings if item.startswith(f"{source.id}:")), None)
        sources.append(SourceRunStatus(source_id=source.id, success=warning is None, warning=warning))
    return StageReport(
        sources=sources,
        warnings=warnings,
        metadata_llm_calls=metadata.llm_calls,
        metadata_llm_success_rate=metadata.success_rate,
        metadata_degraded_count=metadata.degraded_count,
    )


def _fixture_candidates(now: datetime) -> list[Candidate]:
    return [
        Candidate(
            kind="paper",
            source_id="arxiv",
            title="Two-Tower Retrieval for Content Recommendation",
            url="https://arxiv.org/abs/2608.01234",
            published_at=now,
            authors=("Ada Lovelace",),
            excerpt="A fixture paper about retrieval and ranking.",
            arxiv_id="2608.01234",
            categories=("cs.IR",),
            source_weight=1.0,
            metadata_score=0.95,
        ),
        Candidate(
            kind="blog",
            source_id="meta_engineering",
            title="How We Improved Feed Ranking",
            url="https://engineering.example.com/posts/feed-ranking",
            published_at=now,
            authors=("Example Engineer",),
            excerpt="A fixture engineering article about feed ranking.",
            source_weight=1.0,
            source_scenarios=("text_feed",),
            metadata_score=0.90,
        ),
    ]


class _FixtureContent:
    def __init__(self, root: Path) -> None:
        self.paper_html = (root / "fixtures" / "content" / "paper.html").read_text(encoding="utf-8")
        self.article_html = (root / "fixtures" / "content" / "article.html").read_text(encoding="utf-8")

    def fetch_text(self, _url: str, _limit: int) -> str:
        return self.paper_html

    def fetch_bytes(self, _url: str, _limit: int) -> bytes:
        raise RuntimeError("fixture paper PDF is intentionally unavailable")

    def extract_pdf(self, _path: Path, _max_pages: int) -> tuple[str, list[Any]]:
        raise RuntimeError("fixture paper PDF is intentionally unavailable")

    def critical_pages(self, _pages: list[Any]) -> list[int]:
        return []

    def render_pages(self, _path: Path, _pages: list[int], _directory: Path) -> list[Path]:
        return []

    def extract_article(self, html: str) -> str:
        return html.replace("<", " ").replace(">", " ")

    def feed_content(self, candidate: Candidate) -> str | None:
        return candidate.excerpt

    def fetch_article_html(self, _candidate: Candidate) -> str:
        return self.article_html


def _fixture_services(root: Path, work: Path) -> DeepReadServices:
    content = _FixtureContent(root)

    def text_reader(kind: str, _body: str, _context: dict[str, Any]) -> dict[str, Any]:
        if kind == "paper":
            return {
                "problem_zh": "验证候选召回效果。",
                "contributions_zh": ["提供 fixture 结构化分析。"],
                "method_zh": "Two-Tower Retrieval",
                "experiments": {"datasets": ["FixtureSet"], "metrics": ["Recall@20"]},
                "evidence_refs": [{"section": "Method", "page": 1}],
            }
        return {
            "system_context_zh": "内容流排序服务。",
            "architecture_zh": "召回后进行排序。",
            "implementation_zh": "使用离线特征。",
            "evidence_refs": [{"heading": "Architecture"}],
        }

    return DeepReadServices(
        content=ContentServices(
            fetch_text=content.fetch_text,
            fetch_bytes=content.fetch_bytes,
            extract_pdf=content.extract_pdf,
            critical_pages=content.critical_pages,
            render_pages=content.render_pages,
            extract_article=content.extract_article,
            feed_content=content.feed_content,
            fetch_article_html=content.fetch_article_html,
        ),
        temporary_root=work / "temporary",
        text_reader=text_reader,
        vision_reader=lambda _pages: {"architecture_zh": "fixture architecture"},
    )


def _full_read_limiter(config: AppConfig) -> RateLimiter:
    limits = config.settings.limits
    return RateLimiter(
        target_rpm=limits.nvidia_target_rpm,
        hard_rpm=limits.nvidia_hard_rpm,
        min_interval_seconds=limits.nvidia_min_interval_seconds_per_worker,
    )


def _real_services(
    config: AppConfig,
    root: Path,
    work: Path,
    *,
    limiter: RateLimiter | None = None,
) -> DeepReadServices:
    shared_limiter = limiter or _full_read_limiter(config)
    text_client = TextClient.from_config(config.models, limiter=shared_limiter)
    vision_client = VisionClient.from_config(config.models, limiter=shared_limiter)
    profile = config.models.text.active()

    def text_reader(kind: str, body: str, context: dict[str, Any]) -> dict[str, Any]:
        prompt = f"Return strict JSON for a {kind} recommendation-system deep reading. Context: {context}"
        budget = TokenBudget(
            context_window_tokens=profile.context_window_tokens,
            reserved_prompt_tokens=config.models.text.reserved_prompt_tokens,
            reserved_output_tokens=config.models.text.reserved_output_tokens,
        )
        bounded_body = budget.fit_sections([{"heading": "source material", "text": body, "importance": 1}])
        return text_client.complete_json(json_messages(prompt, [bounded_body]), {"type": "object"})

    def vision_reader(paths: list[Path]) -> dict[str, Any]:
        images = ["data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii") for path in paths]
        return vision_client.analyze("Analyze all detected key pages and return strict JSON.", images)

    request_timeout = config.settings.limits.request_timeout_seconds
    max_pdf_bytes = config.settings.limits.max_pdf_bytes
    max_blog_html_bytes = config.settings.limits.max_blog_html_bytes

    def configured_fetch_text(url: str, limit: int) -> str:
        return fetch_text_request(url, min(limit, max_pdf_bytes), timeout=request_timeout)

    def configured_fetch_bytes(url: str, limit: int) -> bytes:
        return fetch_bytes_request(url, min(limit, max_pdf_bytes), timeout=request_timeout)

    def configured_fetch_article_html(candidate: Candidate, limit: int | None = None) -> str:
        return fetch_article_html_request(candidate, min(limit or max_blog_html_bytes, max_blog_html_bytes), timeout=request_timeout)

    source_urls = {source.id: source.url for source in config.sources.blogs if source.enabled}

    def fetch_blog_feed(_source_id: str, url: str) -> bytes:
        return fetch_public_url(url, timeout=request_timeout).content

    blog_feed_cache = BlogFeedCache(
        source_urls,
        fetch_blog_feed,
        max_requests_per_source=max(1, config.settings.limits.rss_requests_per_run_per_source - 1),
    )

    return DeepReadServices(
        content=ContentServices(
            fetch_text=configured_fetch_text,
            fetch_bytes=configured_fetch_bytes,
            fetch_article_html=configured_fetch_article_html,
        ),
        temporary_root=work / "temporary",
        text_reader=text_reader,
        vision_reader=vision_reader,
        max_pdf_bytes=config.settings.limits.max_pdf_bytes,
        max_pdf_pages=config.settings.limits.max_pdf_pages,
        max_html_bytes=config.settings.limits.max_blog_html_bytes,
        domain_limiter=DomainRateLimiter(config.settings.limits.blog_min_interval_seconds_per_domain),
        vision_profile=config.models.vision.profile,
        vision_model=config.models.vision.model,
        blog_feed_content=blog_feed_cache.get,
    )


def _run_deep_read(
    kind: str,
    stage_one: Path,
    output: Path,
    services: DeepReadServices,
    run_id: str,
    *,
    max_candidates: int = 16,
) -> None:
    candidates = [
        json.loads(line)
        for line in (stage_one / f"{kind}s.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    input_dir = output / "candidate-input"
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
        (input_dir / f"{kind}-candidates.json").write_text(json.dumps(candidates, ensure_ascii=False), encoding="utf-8")
        deep_read(kind, input_dir, output, services=services, max_candidates=max_candidates)
        write_json(output / "manifest.json", {"run_id": run_id, "schema_version": "1"})
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)


@app.command("collect-filter")
def collect_filter(output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    state_path = repository / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    result = collect_candidates(config, state)
    candidates = prefilter(result.candidates, config, state)
    limiter = _full_read_limiter(config)
    text_client = TextClient.from_config(config.models, limiter=limiter, timeout_seconds=None, retries=None)
    metadata = analyze_metadata(candidates, config, text_client.complete_json)
    _write_stage_one(
        output,
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
        candidates,
        result.source_states,
        metadata,
        _collection_stage_report(config, result, metadata),
    )


@app.command("deep-read")
def deep_read_command(kind: str = typer.Option(...), input: Path = typer.Option(...), output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    manifest = json.loads((input / "manifest.json").read_text(encoding="utf-8"))
    _run_deep_read(
        kind,
        input,
        output,
        _real_services(config, repository, output),
        manifest["run_id"],
        max_candidates=config.settings.limits.deep_reading_candidates_per_type,
    )


@app.command("rank-integrate")
def rank_integrate(input: Path = typer.Option(...), output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    state_path = repository / "data" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else None
    integrate(
        StageInputs(input / "stage-1", input / "deep-reading-paper", input / "deep-reading-blog"),
        output,
        config,
        state,
        repository_data=repository / "data",
    )


@app.command("run")
def run_pipeline(output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    work = output.parent / "stages"
    collect_filter(work / "stage-1", repository)
    manifest = json.loads((work / "stage-1" / "manifest.json").read_text(encoding="utf-8"))
    config = load_config(repository)
    limiter = _full_read_limiter(config)
    max_candidates = config.settings.limits.deep_reading_candidates_per_type
    _run_deep_read(
        "paper", work / "stage-1", work / "deep-reading-paper",
        _real_services(config, repository, work, limiter=limiter), manifest["run_id"],
        max_candidates=max_candidates,
    )
    _run_deep_read(
        "blog", work / "stage-1", work / "deep-reading-blog",
        _real_services(config, repository, work, limiter=limiter), manifest["run_id"],
        max_candidates=max_candidates,
    )
    rank_integrate(work, output, repository)


@app.command("test-fixtures")
def test_fixtures(case: str = typer.Option("cold-start"), work: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    if case == "failures":
        raise typer.ClickException("fixture failure injected before canonical promotion")
    now = datetime(2026, 8, 10, tzinfo=UTC)
    run_id = f"fixture-{case}"
    stage_one = work / "stage-1"
    candidates = _fixture_candidates(now)
    _write_stage_one(stage_one, run_id, candidates)
    services = _fixture_services(repository, work)
    _run_deep_read("paper", stage_one, work / "deep-reading-paper", services, run_id)
    _run_deep_read("blog", stage_one, work / "deep-reading-blog", services, run_id)
    integrate(StageInputs(stage_one, work / "deep-reading-paper", work / "deep-reading-blog"), work / "publish-bundle", config, state=None)
