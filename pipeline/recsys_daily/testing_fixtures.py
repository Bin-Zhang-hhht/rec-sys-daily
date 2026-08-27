"""Deterministic, runtime-only pipeline scenarios for tests and CI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from collections.abc import Mapping, Sequence
import html
import json
from pathlib import Path
import re
import shutil
import socket
from typing import Any

from .artifacts import write_json
from .collect import Candidate, FeedResponse, stable_id
from .config import AppConfig, load_config
from .content import BlogFeedCache, ContentServices
from .deep_read import DeepReadServices, deep_read
from .integrate import StageInputs, integrate
from .schemas import BuildConfigSnapshot, RunReport, StageReport, State
from .similarity import run_similarity
from .stage_one import load_history_ids, run_collect_filter


PAPER_HTML = """<!doctype html><html><body><article><h1>Two-Tower Retrieval for Content Recommendation</h1><h2>Method</h2><p>The model uses two towers.</p></article></body></html>"""
ARTICLE_HTML = """<!doctype html><html><body><article><h1>How We Improved Feed Ranking</h1><h2>Architecture</h2><p>A bounded article about ranking.</p></article></body></html>"""


class _FixtureTokenizer:
    def encode(self, value: str) -> list[int]:
        return [ord(character) for character in value]

    def decode(self, value: Sequence[int]) -> str:
        return "".join(chr(token) for token in value)


class _FixtureEmbedder:
    def embed(self, values: Sequence[str], *, batch_size: int) -> Sequence[Sequence[float]]:
        del batch_size
        return [[1.0] + [0.0] * 383 for _ in values]


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
    summary_zh = (
        "研究面向推荐系统的双塔候选召回与排序方法。"
        if candidate.kind == "paper"
        else "介绍推荐系统信息流排序的工程实现与实践经验。"
    )
    return {
        "id": stable_id(candidate),
        "summary_zh": summary_zh,
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[1].id],
        "relevance_score": candidate.metadata_score,
        "degraded": degraded,
    }


class _FixtureContent:
    def __init__(self, *, exercise_blog_fallbacks: bool = False) -> None:
        self.paper_html = PAPER_HTML
        self.article_html = ARTICLE_HTML
        self.exercise_blog_fallbacks = exercise_blog_fallbacks

    def fetch_bytes(self, _url: str, _limit: int) -> bytes:
        return b"fixture PDF"

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
                "problem_zh": "研究推荐系统中的候选召回问题。",
                "contributions_zh": ["提出有界的双塔候选召回方案。"],
                "method_zh": "采用双塔模型执行候选召回。",
                "experiments": {"datasets": ["RuntimeFixtureSet"], "metrics": ["Recall@20"]},
                "evidence_refs": [{"section": "Method", "page": 1}],
            }
        return {
            "system_context_zh": "面向推荐系统的信息流排序服务。",
            "architecture_zh": "先召回候选，再执行排序。",
            "implementation_zh": "使用有界的在线特征服务。",
            "evidence_refs": [{"heading": "Architecture"}],
        }

    class FixtureMinerU:
        max_pdf_bytes = 20_971_520

        @staticmethod
        def parse_pdf(_pdf: bytes, _filename: str, _temporary_root: Path) -> str:
            return "# Method\nTwo-Tower Retrieval\n# Results\nRecall@20 improves."

    services = DeepReadServices(
        content=ContentServices(
            fetch_bytes=content.fetch_bytes,
            extract_article=content.extract_article,
            feed_content=content.feed_content,
            fetch_article_html=content.fetch_article_html,
        ),
        temporary_root=work / "temporary",
        text_reader=text_reader,
        mineru=FixtureMinerU(),
    )
    if exercise_blog_fallbacks:
        def failed_second_feed(_source_id: str, _url: str) -> bytes:
            raise RuntimeError("fixture second Feed unavailable")

        services.blog_feed_content = BlogFeedCache(
            {"meta_engineering": "https://engineering.example.com/feed"},
            failed_second_feed,
        ).get
    return services


def _public_resolver(_host: str, port: int, *_args: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port))]


def _fixture_metadata_candidate_ids(messages: Sequence[Mapping[str, Any]]) -> list[str]:
    """Read candidate IDs from current prompts and the upcoming JSON envelope."""
    for message in messages:
        content = message.get("content")
        payload: Any = content
        if isinstance(content, str):
            try:
                payload = json.loads(content)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, Mapping) or "source_documents" not in payload:
            continue
        documents = payload["source_documents"]
        if not isinstance(documents, list):
            raise ValueError("fixture source_documents must be a list")
        ids: list[str] = []
        for document in documents:
            if not isinstance(document, Mapping) or not isinstance(document.get("id"), str) or not document["id"].strip():
                raise ValueError("fixture source document must contain a non-empty id")
            ids.append(document["id"])
        return ids

    legacy = "\n".join(str(message.get("content", "")) for message in messages)
    return re.findall(r"^id: (.+)$", legacy, re.MULTILINE)


def _atom_document(candidates: list[Candidate]) -> str:
    entries = []
    for candidate in candidates:
        arxiv_id = candidate.arxiv_id or stable_id(candidate).removeprefix("arxiv-")
        entries.append(
            "<entry>"
            f"<id>http://arxiv.org/abs/{html.escape(arxiv_id)}v2</id>"
            f"<published>{candidate.published_at.isoformat().replace('+00:00', 'Z')}</published>"
            f"<title>{html.escape(candidate.title)}</title>"
            f"<summary>{html.escape(candidate.excerpt)}</summary>"
            + "".join(f"<author><name>{html.escape(author)}</name></author>" for author in candidate.authors)
            + f"<link href='{html.escape(candidate.url or '')}' rel='alternate'/>"
            + "".join(f"<category term='{html.escape(category)}'/>" for category in candidate.categories)
            + "</entry>"
        )
    return "<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>" + "".join(entries) + "</feed>"


def _rss_document(candidates: list[Candidate]) -> str:
    items = []
    for candidate in candidates:
        items.append(
            "<item>"
            f"<guid>{html.escape(candidate.source_entry_id or candidate.url or stable_id(candidate))}</guid>"
            f"<title>{html.escape(candidate.title)}</title>"
            f"<link>{html.escape(candidate.url or '')}</link>"
            f"<pubDate>{candidate.published_at.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
            f"<description>{html.escape(candidate.excerpt)}</description>"
            + "".join(f"<author>{html.escape(author)}</author>" for author in candidate.authors)
            + "</item>"
        )
    return "<?xml version='1.0'?><rss version='2.0'><channel>" + "".join(items) + "</channel></rss>"


def _fixture_stage_dependencies(
    config: AppConfig,
    candidates: list[Candidate],
    *,
    fail_metadata_batch: int | None = None,
    exercise_source_fallbacks: bool = False,
) -> tuple[Any, Any]:
    papers = [candidate for candidate in candidates if candidate.kind == "paper"]
    blogs = [candidate for candidate in candidates if candidate.kind == "blog"]
    enabled_blogs = [source for source in config.sources.blogs if source.enabled]
    blogs_by_url: dict[str, list[Candidate]] = {source.url: [] for source in enabled_blogs}
    source_failure_urls = (
        {
            source.url: warning
            for source, warning in zip(
                enabled_blogs[1:3],
                ("second Feed failed", "article extraction fallback"),
                strict=False,
            )
        }
        if exercise_source_fallbacks
        else {}
    )
    candidate_sources = [source for source in enabled_blogs if source.url not in source_failure_urls]
    for index, candidate in enumerate(blogs):
        blogs_by_url[candidate_sources[index % len(candidate_sources)].url].append(candidate)

    def fetcher(url: str, _headers: dict[str, str]) -> FeedResponse:
        if "export.arxiv.org" in url:
            return FeedResponse(200, _atom_document(papers).encode(), {})
        if url in source_failure_urls:
            raise RuntimeError(source_failure_urls[url])
        return FeedResponse(200, _rss_document(blogs_by_url[url]).encode(), {})

    candidates_by_id = {stable_id(candidate): candidate for candidate in candidates}
    metadata_calls = 0

    def complete_json(messages: Any, _schema: Any) -> dict[str, Any]:
        nonlocal metadata_calls
        metadata_calls += 1
        if fail_metadata_batch is not None and metadata_calls == fail_metadata_batch:
            raise RuntimeError("fixture metadata degradation")
        ids = _fixture_metadata_candidate_ids(messages)
        items = []
        for item_id in ids:
            value = _metadata(candidates_by_id[item_id], config)
            items.append(value)
        return {"items": items}

    return fetcher, complete_json


def _run_stage_one(
    root: Path,
    config: AppConfig,
    candidates: list[Candidate],
    run_id: str,
    *,
    state: State | None = None,
    repository_data: Path | None = None,
    degraded: bool = False,
) -> Path:
    fetcher, complete_json = _fixture_stage_dependencies(
        config,
        candidates,
        fail_metadata_batch=2 if degraded else None,
        exercise_source_fallbacks=degraded,
    )
    stage = root / "stage-1"
    history = load_history_ids(repository_data or root / "repository-data", config, state)
    run_collect_filter(
        config,
        stage,
        state,
        history,
        complete_json,
        fetcher=fetcher,
        resolver=_public_resolver,
        now=datetime(2026, 8, 10, tzinfo=UTC),
        run_id=run_id,
    )
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
        deep_read(kind, input_dir, output, services=services, max_candidates=20)
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
    exercise_blog_fallbacks: bool = False,
) -> FixtureScenarioResult:
    stage = _run_stage_one(
        root,
        config,
        candidates,
        run_id,
        state=state,
        repository_data=repository_data,
        degraded=exercise_blog_fallbacks,
    )
    deep_dirs = _write_deep_stages(
        root,
        stage,
        run_id,
        exercise_blog_fallbacks=exercise_blog_fallbacks,
    )
    similarity = root / "similarity"
    run_similarity(
        stage,
        deep_dirs["paper"],
        deep_dirs["blog"],
        repository_data,
        similarity,
        config,
        embedder=_FixtureEmbedder(),
        tokenizer=_FixtureTokenizer(),
    )
    bundle = integrate(StageInputs(stage, deep_dirs["paper"], deep_dirs["blog"], similarity), root / "publish-bundle", config, state=state, repository_data=repository_data)
    pending_state = json.loads((bundle.path / "pending-data/state.json").read_text(encoding="utf-8"))
    stage_candidate_count = sum(
        len([line for line in (stage / f"{kind}s.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()])
        for kind in ("paper", "blog")
    )
    historical_count = len(list((bundle.path / "pending-data/items").rglob("*.json"))) - stage_candidate_count
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
    seed = State(
        schema_version="1",
        last_success_at=datetime(2026, 8, 9, tzinfo=UTC),
        recommended_item_ids=["seed-item"],
    )
    write_json(state_path, seed.model_dump(mode="json"))
    state_before = state_path.read_bytes()
    completed: list[str] = []
    promoted = False
    try:
        _inject_failure(failure_point, "collect")
        stage = _run_stage_one(root, config, candidates, f"fixture-failure-{failure_point}", state=seed)
        completed.append("collect")

        _inject_failure(failure_point, "deep-read")
        deep_dirs = _write_deep_stages(root, stage, f"fixture-failure-{failure_point}")
        completed.append("deep-read")

        _inject_failure(failure_point, "similarity")
        similarity = root / "similarity"
        run_similarity(
            stage,
            deep_dirs["paper"],
            deep_dirs["blog"],
            None,
            similarity,
            config,
            embedder=_FixtureEmbedder(),
            tokenizer=_FixtureTokenizer(),
        )

        _inject_failure(failure_point, "rank")
        bundle = integrate(
            StageInputs(stage, deep_dirs["paper"], deep_dirs["blog"], similarity),
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
    published_at = datetime(2026, 8, 10, tzinfo=UTC)
    item = {
        "id": "arxiv-2608.01234",
        "kind": "paper",
        "source": "arxiv",
        "title": "Two-Tower Retrieval Paper 0",
        "url": "https://arxiv.org/abs/2608.01234",
        "published_at": published_at.isoformat().replace("+00:00", "Z"),
        "authors": ["Historical Author"],
        "abstract": "Candidate retrieval and recommendation ranking.",
        "arxiv_id": "2608.01234",
        "doi": None,
        "summary_zh": "历史推荐论文摘要。",
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "relevance_score": 0.8,
        "final_score": 0.8,
        "llm": {
            "model": config.models.text.model,
            "generated_at": published_at.isoformat().replace("+00:00", "Z"),
            "degraded": True,
        },
        "deep_reading": {
            "analysis_basis": "abstract_fallback",
        },
    }
    write_json(data / "items/papers/2026/08/arxiv-2608.01234.json", item)
    write_json(data / "digests/2026/08/2026-08-10.json", {
        "date": "2026-08-10",
        "papers": [{"item_id": "arxiv-2608.01234", "recommendation_reason_zh": "历史推荐", "rank": 1}],
        "blogs": [],
    })
    storage = config.settings.storage
    snapshot = BuildConfigSnapshot(
        graph_initial_content_nodes=config.settings.graph_initial_content_nodes,
        graph_shard_target_bytes=config.settings.graph_shard_target_bytes,
        minimum_final_score=config.settings.minimum_final_score,
        minimum_metadata_relevance_score=config.settings.minimum_metadata_relevance_score,
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
    write_json(data / "runs/2026/08/historical-run.json", report.model_dump(mode="json"))
    state = State(last_success_at=published_at, recommended_item_ids=["arxiv-2608.01234"], updated_at=published_at)
    write_json(data / "state.json", state.model_dump(mode="json"))
    return state


def _scenario(work: Path, name: str, config: AppConfig, repository_root: Path) -> FixtureScenarioResult:
    root = work / "generated" / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    if name == "failures":
        seed = {
            "schema_version": "1",
            "last_success_at": "2026-08-09T00:00:00Z",
            "recommended_item_ids": ["seed-item"],
        }
        candidates = [_candidate("paper", 0, now), _candidate("blog", 0, now)]
        failures = {
            failure_point: _run_failure_injection(root / failure_point, config, candidates, failure_point)
            for failure_point in ("collect", "deep-read", "similarity", "rank", "site", "deploy")
        }
        return FixtureScenarioResult(name, root, None, seed, 0, {}, None, failures)
    if name == "daily":
        data = root / "repository-data"
        state = _seed_historical_repository(data, config)
        return _run_pipeline(root, config, [_candidate("paper", 0, now), _candidate("blog", 0, now)], "fixture-daily", state=state, repository_data=data)
    if name == "degraded":
        fallback_terms = "content recommendation feed ranking candidate retrieval two-tower model"
        blogs = [
            source.model_copy(update={"scenarios": [fallback_terms]})
            if source.id == "pinterest_engineering"
            else source
            for source in config.sources.blogs
        ]
        config = config.model_copy(update={"sources": config.sources.model_copy(update={"blogs": blogs})})
        candidates = [_candidate("paper" if index % 2 == 0 else "blog", index, now) for index in range(100)]
        return _run_pipeline(
            root,
            config,
            candidates,
            "fixture-degraded",
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
