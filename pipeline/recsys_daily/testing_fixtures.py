"""Deterministic, runtime-only pipeline scenarios for tests and CI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any

from .artifacts import write_json, write_jsonl
from .collect import Candidate, stable_id
from .config import AppConfig, load_config
from .content import BlogFeedCache, ContentServices, PageText
from .deep_read import DeepReadServices, deep_read
from .integrate import StageInputs, integrate
from .schemas import BuildConfigSnapshot, RunReport, SourceRunStatus, StageReport, State


PAPER_HTML = """<!doctype html><html><body><article><h1>Two-Tower Retrieval for Content Recommendation</h1><h2>Method</h2><p>The model uses two towers.</p></article></body></html>"""
ARTICLE_HTML = """<!doctype html><html><body><article><h1>How We Improved Feed Ranking</h1><h2>Architecture</h2><p>A bounded article about ranking.</p></article></body></html>"""
ARXIV_ATOM = """<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><entry><id>http://arxiv.org/abs/2608.01234v2</id><published>2026-08-09T12:00:00Z</published><title>Two-Tower Retrieval for Content Recommendation</title><summary>Candidate retrieval for content recommendation with a Two-Tower Model.</summary><author><name>Ada Lovelace</name></author><link href='https://arxiv.org/abs/2608.01234v2' rel='alternate'/></entry></feed>"""
BLOG_RSS = """<?xml version='1.0'?><rss version='2.0'><channel><item><guid>example-ranking-2026</guid><title>How We Improved Feed Ranking</title><link>https://engineering.example.com/posts/feed-ranking</link><pubDate>Sun, 09 Aug 2026 08:30:00 +0000</pubDate><description><![CDATA[Practical feed ranking lessons.]]></description></item></channel></rss>"""


@dataclass(frozen=True)
class FixtureScenarioResult:
    name: str
    generated_root: Path
    publish_bundle: Path | None
    pending_state: dict[str, Any]
    historical_item_count: int
    stage_report: dict[str, Any]
    promoted_state: dict[str, Any] | None
    failure_injections: dict[str, "FailureInjectionEvidence"] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureInjectionEvidence:
    failure_point: str
    state_path: Path
    state_before: bytes
    state_after: bytes
    promoted: bool
    completed_boundaries: tuple[str, ...]


def _candidate(kind: str, index: int, now: datetime) -> Candidate:
    if kind == "paper":
        arxiv_id = f"2608.{1234 + index:05d}"
        return Candidate(
            kind="paper", source_id="arxiv", title=f"Two-Tower Retrieval Paper {index}",
            url=f"https://arxiv.org/abs/{arxiv_id}", published_at=now,
            authors=("Ada Lovelace",), excerpt="Candidate retrieval and recommendation ranking.",
            arxiv_id=arxiv_id, categories=("cs.IR",), source_weight=1.0, metadata_score=0.9,
        )
    return Candidate(
        kind="blog", source_id="meta_engineering", title=f"Feed Ranking Engineering {index}",
        url=f"https://engineering.example.com/posts/feed-ranking-{index}", published_at=now,
        authors=("Example Engineer",), excerpt="Practical feed ranking implementation lessons.",
        source_weight=1.0, source_scenarios=("text_feed",), metadata_score=0.85,
    )


def _metadata(candidate: Candidate, config: AppConfig, *, degraded: bool = False) -> dict[str, Any]:
    return {
        "id": stable_id(candidate),
        "summary_zh": candidate.excerpt,
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[1].id],
        "relevance_score": candidate.metadata_score,
        "graph_relations": [],
        "degraded": degraded,
    }


class _FixtureContent:
    def __init__(self, *, exercise_blog_fallbacks: bool = False) -> None:
        self.paper_html = PAPER_HTML
        self.article_html = ARTICLE_HTML
        self.exercise_blog_fallbacks = exercise_blog_fallbacks

    def fetch_text(self, _url: str, _limit: int) -> str:
        return self.paper_html

    def fetch_bytes(self, _url: str, _limit: int) -> bytes:
        raise RuntimeError("fixture PDF unavailable; HTML fallback is expected")

    def extract_pdf(self, _path: Path, _max_pages: int) -> tuple[str, list[PageText]]:
        raise RuntimeError("fixture PDF unavailable")

    def critical_pages(self, _pages: list[PageText]) -> list[int]:
        return []

    def render_pages(self, _path: Path, _pages: list[int], _directory: Path) -> list[Path]:
        return []

    def extract_article(self, html: str) -> str:
        return html.replace("<", " ").replace(">", " ")

    def feed_content(self, _candidate: Candidate) -> str | None:
        return None

    def fetch_article_html(self, candidate: Candidate, _limit: int | None = None) -> str:
        if self.exercise_blog_fallbacks and int(candidate.url.rsplit("-", 1)[-1]) % 4 == 3:
            raise RuntimeError("fixture article unavailable; excerpt fallback is expected")
        return self.article_html


def _fixture_services(work: Path, *, exercise_blog_fallbacks: bool = False) -> DeepReadServices:
    content = _FixtureContent(exercise_blog_fallbacks=exercise_blog_fallbacks)

    def text_reader(kind: str, _body: str, _context: dict[str, Any]) -> dict[str, Any]:
        if kind == "paper":
            return {
                "problem_zh": "Candidate retrieval for recommendation.",
                "contributions_zh": ["A bounded two-tower retrieval design."],
                "method_zh": "Two-Tower Retrieval",
                "experiments": {"datasets": ["RuntimeFixtureSet"], "metrics": ["Recall@20"]},
                "evidence_refs": [{"section": "Method", "page": 1}],
            }
        return {
            "system_context_zh": "Feed ranking service.",
            "architecture_zh": "Retrieve candidates then rank them.",
            "implementation_zh": "Bounded online feature service.",
            "evidence_refs": [{"heading": "Architecture"}],
        }

    services = DeepReadServices(
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
        vision_reader=lambda _pages: {},
    )
    if exercise_blog_fallbacks:
        def failed_second_feed(_source_id: str, _url: str) -> bytes:
            raise RuntimeError("fixture second Feed unavailable")

        services.blog_feed_content = BlogFeedCache(
            {"meta_engineering": "https://engineering.example.com/feed"},
            failed_second_feed,
        ).get
    return services


def _write_sources(root: Path) -> None:
    for relative, value in {
        "sources/arxiv.atom": ARXIV_ATOM,
        "sources/blog.rss": BLOG_RSS,
        "content/paper.html": PAPER_HTML,
        "content/article.html": ARTICLE_HTML,
        "models/responses.json": json.dumps({"paper": "structured", "blog": "structured"}),
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")


def _write_stage(
    root: Path,
    candidates: list[Candidate],
    config: AppConfig,
    run_id: str,
    *,
    report: StageReport,
    metadata_overrides: dict[str, dict[str, Any]] | None = None,
) -> Path:
    stage = root / "stage-1"
    stage.mkdir(parents=True, exist_ok=True)
    write_json(stage / "manifest.json", {"run_id": run_id, "schema_version": "1"})
    docs = []
    for candidate in candidates:
        metadata = _metadata(candidate, config)
        metadata.update((metadata_overrides or {}).get(stable_id(candidate), {}))
        value = {
            "kind": candidate.kind,
            "source_id": candidate.source_id,
            "title": candidate.title,
            "url": candidate.url,
            "published_at": candidate.published_at.isoformat().replace("+00:00", "Z"),
            "authors": list(candidate.authors),
            "excerpt": candidate.excerpt,
            "categories": list(candidate.categories),
            "arxiv_id": candidate.arxiv_id,
            "doi": candidate.doi,
            "source_entry_id": candidate.source_entry_id,
            "source_weight": candidate.source_weight,
            "source_scenarios": list(candidate.source_scenarios),
            **metadata,
        }
        docs.append(value)
    for kind in ("paper", "blog"):
        write_jsonl(stage / f"{kind}s.jsonl", [value for value in docs if value["kind"] == kind])
    write_json(stage / "source-states.json", {})
    write_json(stage / "stage-report.json", report.model_dump(mode="json"))
    return stage


def _write_deep_stages(
    root: Path,
    stage: Path,
    run_id: str,
    *,
    exercise_blog_fallbacks: bool = False,
) -> dict[str, Path]:
    services = _fixture_services(root, exercise_blog_fallbacks=exercise_blog_fallbacks)
    deep_dirs: dict[str, Path] = {}
    for kind in ("paper", "blog"):
        input_dir = root / f"candidate-input-{kind}"
        input_dir.mkdir(parents=True, exist_ok=True)
        values = [json.loads(line) for line in (stage / f"{kind}s.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        (input_dir / f"{kind}-candidates.json").write_text(json.dumps(values), encoding="utf-8")
        output = root / f"deep-reading-{kind}"
        deep_read(kind, input_dir, output, services=services, max_candidates=16)
        write_json(output / "manifest.json", {"run_id": run_id, "schema_version": "1"})
        shutil.rmtree(input_dir, ignore_errors=True)
        deep_dirs[kind] = output
    return deep_dirs


def _run_pipeline(
    root: Path,
    config: AppConfig,
    candidates: list[Candidate],
    run_id: str,
    *,
    state: State | None = None,
    repository_data: Path | None = None,
    report: StageReport | None = None,
    metadata_overrides: dict[str, dict[str, Any]] | None = None,
    exercise_blog_fallbacks: bool = False,
) -> FixtureScenarioResult:
    report = report or StageReport(metadata_llm_calls=1, metadata_llm_success_rate=1.0)
    stage = _write_stage(root, candidates, config, run_id, report=report, metadata_overrides=metadata_overrides)
    deep_dirs = _write_deep_stages(
        root,
        stage,
        run_id,
        exercise_blog_fallbacks=exercise_blog_fallbacks,
    )
    bundle = integrate(StageInputs(stage, deep_dirs["paper"], deep_dirs["blog"]), root / "publish-bundle", config, state=state, repository_data=repository_data)
    pending_state = json.loads((bundle.path / "pending-data/state.json").read_text(encoding="utf-8"))
    historical_count = len(list((bundle.path / "pending-data/items").rglob("*.json"))) - len(candidates)
    return FixtureScenarioResult(
        name=root.name,
        generated_root=root,
        publish_bundle=bundle.path,
        pending_state=pending_state,
        historical_item_count=max(0, historical_count),
        stage_report=json.loads((stage / "stage-report.json").read_text(encoding="utf-8")),
        promoted_state=None,
    )


class _InjectedFailure(RuntimeError):
    pass


def _inject_failure(failure_point: str, current_boundary: str) -> None:
    if failure_point == current_boundary:
        raise _InjectedFailure(current_boundary)


def _run_failure_injection(
    root: Path,
    config: AppConfig,
    candidates: list[Candidate],
    failure_point: str,
) -> FailureInjectionEvidence:
    state_path = root / "data/state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    seed = State(schema_version="1", recommended_item_ids=["seed-item"])
    write_json(state_path, seed.model_dump(mode="json"))
    state_before = state_path.read_bytes()
    completed: list[str] = []
    promoted = False
    try:
        _inject_failure(failure_point, "collect")
        stage = _write_stage(
            root,
            candidates,
            config,
            f"fixture-failure-{failure_point}",
            report=StageReport(metadata_llm_calls=1, metadata_llm_success_rate=1.0),
        )
        completed.append("collect")

        _inject_failure(failure_point, "deep-read")
        deep_dirs = _write_deep_stages(root, stage, f"fixture-failure-{failure_point}")
        completed.append("deep-read")

        _inject_failure(failure_point, "rank")
        bundle = integrate(
            StageInputs(stage, deep_dirs["paper"], deep_dirs["blog"]),
            root / "publish-bundle",
            config,
            state=seed,
        )
        completed.append("rank")

        _inject_failure(failure_point, "site")
        if {path.name for path in bundle.path.iterdir()} != {"manifest.json", "taxonomy.json", "pending-data"}:
            raise ValueError("fixture site verification found an invalid publish bundle")
        completed.append("site")

        _inject_failure(failure_point, "deploy")
        state_path.write_bytes((bundle.path / "pending-data/state.json").read_bytes())
        promoted = True
        completed.append("deploy")
    except _InjectedFailure as error:
        if str(error) != failure_point:
            raise
    return FailureInjectionEvidence(
        failure_point=failure_point,
        state_path=state_path,
        state_before=state_before,
        state_after=state_path.read_bytes(),
        promoted=promoted,
        completed_boundaries=tuple(completed),
    )


def _seed_historical_repository(data: Path, config: AppConfig) -> State:
    published_at = datetime(2025, 1, 2, tzinfo=UTC)
    item = {
        "id": "historical-paper",
        "kind": "paper",
        "source": "arxiv",
        "title": "Historical Recommendation Paper",
        "url": "https://arxiv.org/abs/2501.00001",
        "published_at": published_at.isoformat().replace("+00:00", "Z"),
        "authors": ["Historical Author"],
        "summary_zh": "历史推荐论文摘要。",
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {
            "analysis_basis": "abstract_fallback",
            "visual_analysis": {"status": "not_required"},
        },
    }
    write_json(data / "items/papers/2025/01/historical-paper.json", item)
    write_json(data / "digests/2025/01/2025-01-02.json", {
        "date": "2025-01-02",
        "papers": [{"item_id": "historical-paper", "recommendation_reason_zh": "历史推荐", "rank": 1}],
        "blogs": [],
    })
    storage = config.settings.storage
    snapshot = BuildConfigSnapshot(
        graph_max_content_nodes=config.settings.graph_max_content_nodes,
        graph_recent_days=config.settings.graph_recent_days,
        target_item_bytes=storage.target_item_bytes,
        max_item_bytes=storage.max_item_bytes,
        max_blog_excerpt_chars=storage.max_blog_excerpt_chars,
        warn_repository_data_mb=storage.warn_repository_data_mb,
        warn_pages_artifact_mb=storage.warn_pages_artifact_mb,
        fail_pages_artifact_mb=storage.fail_pages_artifact_mb,
    )
    report = RunReport(
        run_id="historical-run",
        started_at=published_at,
        completed_at=published_at,
        config_snapshot=snapshot,
        stage_report=StageReport(),
    )
    write_json(data / "runs/2025/01/historical-run.json", report.model_dump(mode="json"))
    state = State(last_success_at=published_at, recommended_item_ids=["historical-paper"], updated_at=published_at)
    write_json(data / "state.json", state.model_dump(mode="json"))
    return state


def _scenario(work: Path, name: str, config: AppConfig, repository_root: Path) -> FixtureScenarioResult:
    root = work / "generated" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    _write_sources(root)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    if name == "failures":
        seed = {"schema_version": "1", "recommended_item_ids": ["seed-item"]}
        candidates = [_candidate("paper", 0, now), _candidate("blog", 0, now)]
        failures = {
            failure_point: _run_failure_injection(root / failure_point, config, candidates, failure_point)
            for failure_point in ("collect", "deep-read", "rank", "site", "deploy")
        }
        return FixtureScenarioResult(name, root, None, seed, 0, {}, None, failures)
    if name == "daily":
        data = root / "repository-data"
        state = _seed_historical_repository(data, config)
        return _run_pipeline(root, config, [_candidate("paper", 0, now), _candidate("blog", 0, now)], "fixture-daily", state=state, repository_data=data)
    if name == "degraded":
        candidates = [_candidate("paper" if index % 2 == 0 else "blog", index, now) for index in range(100)]
        metadata_overrides = {
            stable_id(candidate): {"degraded": True}
            for candidate in candidates[:8]
        }
        metadata_overrides[stable_id(candidates[0])]["summary_zh"] = None
        metadata_overrides[stable_id(candidates[1])]["methods"] = []
        return _run_pipeline(
            root,
            config,
            candidates,
            "fixture-degraded",
            report=StageReport(
                sources=[SourceRunStatus(source_id="meta_engineering", success=False, warning="optional Feed failed")],
                metadata_llm_calls=13,
                metadata_llm_success_rate=12 / 13,
                metadata_degraded_count=8,
                warnings=["optional Feed failed", "second Feed failed", "article extraction fallback"],
            ),
            metadata_overrides=metadata_overrides,
            exercise_blog_fallbacks=True,
        )
    return _run_pipeline(root, config, [_candidate("paper", 0, now), _candidate("blog", 0, now)], f"fixture-{name}")


def run_fixture_scenarios(work: Path, case: str = "all", repository_root: Path | None = None) -> dict[str, FixtureScenarioResult]:
    root = (repository_root or Path.cwd()).resolve()
    while not (root / "config" / "topics.yaml").exists() and root != root.parent:
        root = root.parent
    config = load_config(root)
    names = ["cold-start", "daily", "degraded", "failures", "site"] if case == "all" else [case]
    allowed = {"cold-start", "daily", "degraded", "failures", "site"}
    if any(name not in allowed for name in names):
        raise ValueError(f"unknown fixture scenario: {case}")
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    results = {name: _scenario(work, name, config, root) for name in names}
    if "site" in results and results["site"].publish_bundle is not None:
        destination = work / "publish-bundle"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(results["site"].publish_bundle, destination)
        result = results["site"]
        results["site"] = FixtureScenarioResult(result.name, result.generated_root, destination, result.pending_state, result.historical_item_count, result.stage_report, result.promoted_state)
    elif len(results) == 1:
        result = next(iter(results.values()))
        if result.publish_bundle is not None:
            destination = work / "publish-bundle"
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(result.publish_bundle, destination)
            results[result.name] = FixtureScenarioResult(result.name, result.generated_root, destination, result.pending_state, result.historical_item_count, result.stage_report, result.promoted_state)
    return results
