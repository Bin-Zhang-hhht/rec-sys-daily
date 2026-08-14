"""The four-stage command line for production and offline fixture runs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

import typer

from .artifacts import write_json
from .collect import Candidate
from .config import AppConfig, load_config
from .content import BlogFeedCache, ContentServices, fetch_article_html as fetch_article_html_request, fetch_bytes as fetch_bytes_request
from .deep_read import DeepReadServices, deep_read
from .integrate import StageInputs, integrate
from .llm import TextClient, TokenBudget
from .mineru import MinerUClient
from .prompts import json_messages
from .rate_limit import DomainRateLimiter
from .security import fetch_public_url
from .schemas import State, blog_reading_json_schema, paper_reading_json_schema
from .stage_one import load_history_ids, run_collect_filter
from .testing_fixtures import run_fixture_scenarios


app = typer.Typer(no_args_is_help=True, add_completion=False)


def _cli_error(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _root(root: Path | None = None) -> Path:
    candidate = (root or Path.cwd()).resolve()
    for path in (candidate, *candidate.parents):
        if (path / "config" / "topics.yaml").exists():
            return path
    raise typer.BadParameter("could not locate config/topics.yaml")


def _load_repository_state(repository: Path) -> State | None:
    state_path = repository / "data" / "state.json"
    if not state_path.exists():
        return None
    try:
        state = State.model_validate(json.loads(state_path.read_text(encoding="utf-8")))
        if state.last_success_at is None:
            raise ValueError("last_success_at is required")
        return state
    except Exception as exc:
        _cli_error(f"invalid data/state.json: {exc}")
        raise AssertionError("unreachable") from exc


def _real_services(
    config: AppConfig,
    root: Path,
    work: Path,
    *,
    kind: str = "paper",
) -> DeepReadServices:
    if kind not in {"paper", "blog"}:
        raise ValueError("kind must be paper or blog")
    text_client = TextClient.from_config(config.models)
    text_model = config.models.text

    def text_reader(kind: str, body: str, context: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            f"Analyze this {kind} for a recommendation-system deep reading. "
            "Summarize only claims supported by the supplied source document. "
            "Return exactly the fields required by the supplied JSON schema, with no extra fields. "
            "Use JSON numbers between 0 and 1 for evidence_quality, business_transferability, "
            "and technical_depth."
        )
        budget = TokenBudget(
            context_window_tokens=text_model.context_window_tokens,
            reserved_prompt_tokens=config.models.text.reserved_prompt_tokens,
            reserved_output_tokens=config.models.text.reserved_output_tokens,
        )
        bounded_body = budget.fit_sections([{"heading": "source material", "text": body, "importance": 1}])
        source_document = {
            "id": context.get("id"),
            "kind": kind,
            "analysis_basis": context.get("analysis_basis"),
            "text": bounded_body,
        }
        source_document = {key: value for key, value in source_document.items() if value is not None}
        schema = paper_reading_json_schema() if kind == "paper" else blog_reading_json_schema()
        return text_client.complete_json(json_messages(prompt, [source_document]), schema)

    request_timeout = config.settings.limits.request_timeout_seconds
    retry_attempts = config.settings.limits.retry_attempts
    retry_backoff_seconds = config.settings.limits.retry_backoff_seconds
    retry_max_delay_seconds = config.settings.limits.retry_max_delay_seconds
    request_user_agent = config.settings.request_user_agent
    max_pdf_bytes = config.models.mineru.max_pdf_bytes
    max_blog_html_bytes = config.settings.limits.max_blog_html_bytes
    arxiv_limiter = DomainRateLimiter(config.settings.limits.arxiv_min_interval_seconds)
    blog_limiter = DomainRateLimiter(config.settings.limits.blog_min_interval_seconds_per_domain)

    def configured_fetch_bytes(url: str, limit: int) -> bytes:
        return fetch_bytes_request(
            url,
            min(limit, max_pdf_bytes),
            timeout=request_timeout,
            max_attempts=retry_attempts,
            user_agent=request_user_agent,
            attempt_limiter=lambda: arxiv_limiter.acquire(url),
            backoff_seconds=retry_backoff_seconds,
            max_delay_seconds=retry_max_delay_seconds,
        )

    def configured_fetch_article_html(candidate: Candidate, limit: int | None = None) -> str:
        return fetch_article_html_request(
            candidate,
            min(limit or max_blog_html_bytes, max_blog_html_bytes),
            timeout=request_timeout,
            max_attempts=retry_attempts,
            user_agent=request_user_agent,
            attempt_limiter=lambda: blog_limiter.acquire(candidate.url or ""),
            backoff_seconds=retry_backoff_seconds,
            max_delay_seconds=retry_max_delay_seconds,
        )

    source_urls = {source.id: source.url for source in config.sources.blogs if source.enabled}

    def fetch_blog_feed(_source_id: str, url: str) -> bytes:
        return fetch_public_url(
            url,
            timeout=request_timeout,
            max_attempts=retry_attempts,
            user_agent=request_user_agent,
            attempt_limiter=lambda: blog_limiter.acquire(url),
            backoff_seconds=retry_backoff_seconds,
            max_delay_seconds=retry_max_delay_seconds,
            max_bytes=max_blog_html_bytes,
        ).content

    blog_feed_cache = BlogFeedCache(source_urls, fetch_blog_feed)

    if kind == "paper":
        mineru_config = config.models.mineru
        mineru = MinerUClient(
            api_key=os.environ.get(mineru_config.api_key_env, ""),
            config=mineru_config,
            max_attempts=retry_attempts,
            backoff_seconds=retry_backoff_seconds,
            max_delay_seconds=retry_max_delay_seconds,
        )
    else:
        mineru = MinerUClient(api_key="unused-in-blog-runner", config=config.models.mineru)

    return DeepReadServices(
        content=ContentServices(
            fetch_bytes=configured_fetch_bytes,
            fetch_article_html=configured_fetch_article_html,
        ),
        temporary_root=work / "temporary",
        text_reader=text_reader,
        mineru=mineru,
        max_html_bytes=config.settings.limits.max_blog_html_bytes,
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
    state = _load_repository_state(repository)
    history = load_history_ids(repository / "data", config, state)
    text_client = TextClient.from_config(config.models, timeout_seconds=None, retries=None)
    run_collect_filter(config, output, state, history, text_client.complete_json)


@app.command("deep-read")
def deep_read_command(kind: str = typer.Option(...), input: Path = typer.Option(...), output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    manifest = json.loads((input / "manifest.json").read_text(encoding="utf-8"))
    _run_deep_read(
        kind,
        input,
        output,
        _real_services(config, repository, output, kind=kind),
        manifest["run_id"],
        max_candidates=config.settings.limits.deep_reading_candidates_per_type,
    )


@app.command("rank-integrate")
def rank_integrate(input: Path = typer.Option(...), output: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    repository = _root(root)
    config = load_config(repository)
    state = _load_repository_state(repository)
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
    max_candidates = config.settings.limits.deep_reading_candidates_per_type
    _run_deep_read(
        "paper", work / "stage-1", work / "deep-reading-paper",
        _real_services(config, repository, work, kind="paper"), manifest["run_id"],
        max_candidates=max_candidates,
    )
    _run_deep_read(
        "blog", work / "stage-1", work / "deep-reading-blog",
        _real_services(config, repository, work, kind="blog"), manifest["run_id"],
        max_candidates=max_candidates,
    )
    rank_integrate(work, output, repository)


@app.command("test-fixtures")
def test_fixtures(case: str = typer.Option("all"), work: Path = typer.Option(...), root: Path = typer.Option(Path("."))) -> None:
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    known_entries = {"generated", "publish-bundle", "manifest.json", "taxonomy.json", "pending-data"}
    unknown_entries = {path.name for path in work.iterdir()} - known_entries
    if unknown_entries:
        names = ", ".join(sorted(unknown_entries))
        _cli_error(f"fixture work directory contains unknown entries: {names}")
    for name in known_entries:
        path = work / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="recsys-daily-fixtures-") as temporary:
        results = run_fixture_scenarios(Path(temporary), case=case, repository_root=_root(root))
        result_name = "site" if case == "all" else case.replace("_", "-")
        result = results.get(result_name)
        if result is None:
            _cli_error(f"fixture scenario did not complete: {case}")
        if result.publish_bundle is None:
            return
        source = result.publish_bundle
        expected = {"manifest.json", "taxonomy.json", "pending-data"}
        actual = {path.name for path in source.iterdir()}
        if actual != expected:
            _cli_error("fixture publish bundle has an invalid top-level contract")
        for name in sorted(expected):
            source_path = source / name
            destination = work / name
            if source_path.is_dir():
                shutil.copytree(source_path, destination)
            else:
                shutil.copy2(source_path, destination)
