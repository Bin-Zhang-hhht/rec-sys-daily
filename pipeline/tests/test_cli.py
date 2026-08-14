from datetime import UTC, datetime
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from recsys_daily.cli import _real_services, _run_deep_read, app
from recsys_daily.collect import Candidate
from recsys_daily.config import load_config
from recsys_daily.deep_read import DeepReadServices
from recsys_daily.schemas import State


runner = CliRunner()


def test_cli_exposes_stage_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "collect-filter", "deep-read", "rank-integrate", "test-fixtures"):
        assert command in result.stdout


def test_fixture_success_writes_publish_bundle_without_network(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test-fixtures", "--case", "cold-start", "--work", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert {path.name for path in tmp_path.iterdir()} == {"manifest.json", "taxonomy.json", "pending-data"}
    assert (tmp_path / "manifest.json").exists()
    assert (tmp_path / "pending-data" / "state.json").exists()


def test_fixture_failure_does_not_write_canonical_state(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test-fixtures", "--case", "failures", "--work", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert not any(tmp_path.iterdir())


def test_fixture_refuses_unknown_work_directory_entries(tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("user content", encoding="utf-8")

    result = runner.invoke(app, ["test-fixtures", "--case", "cold-start", "--work", str(tmp_path)])

    assert result.exit_code != 0
    assert "unknown entries" in result.output
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "user content"


def test_collect_filter_passes_complete_canonical_history_to_stage_one(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    data = tmp_path / "data"
    item = data / "items/papers/2025/01/item-history.json"
    digest = data / "digests/2025/01/2025-01-02.json"
    item.parent.mkdir(parents=True)
    digest.parent.mkdir(parents=True)
    (data / "state.json").write_text(
        json.dumps(State(
            last_success_at=datetime(2026, 8, 9, tzinfo=UTC),
            recommended_item_ids=["state-history"],
        ).model_dump(mode="json")),
        encoding="utf-8",
    )
    item.write_text(json.dumps({
        "id": "item-history",
        "kind": "paper",
        "title": "History",
        "summary_zh": "Historical summary",
        "source": "arxiv",
        "url": "https://arxiv.org/abs/2501.00001",
        "published_at": "2025-01-02T00:00:00Z",
        "authors": ["Author"],
        "abstract": "Historical abstract",
        "arxiv_id": "2501.00001",
        "doi": None,
        "targets": [config.topics.targets[0].id],
        "scenarios": [config.topics.scenarios[0].id],
        "tasks": [config.topics.tasks[0].id],
        "methods": [config.topics.methods[0].id],
        "deep_reading": {"analysis_basis": "abstract_fallback"},
    }), encoding="utf-8")
    digest.write_text(json.dumps({
        "date": "2025-01-02",
        "papers": [{"item_id": "item-history", "recommendation_reason_zh": "history", "rank": 1}],
        "blogs": [],
    }), encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeClient:
        complete_json = staticmethod(lambda *_args: {"items": []})

        @classmethod
        def from_config(cls, *_args: object, **_kwargs: object) -> "FakeClient":
            return cls()

    def fake_run(_config: object, _output: Path, state: object, history: object, _complete_json: object, **_kwargs: object) -> None:
        captured["state"] = state
        captured["history"] = history

    monkeypatch.setattr(cli, "_root", lambda _root: tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _root: config)
    monkeypatch.setattr(cli, "TextClient", FakeClient)
    monkeypatch.setattr(cli, "run_collect_filter", fake_run)

    cli.collect_filter(tmp_path / "stage-1", tmp_path)

    assert isinstance(captured["state"], State)
    assert captured["history"] == {"state-history", "item-history"}


@pytest.mark.parametrize("state_document", ['{"last_success_at":"invalid"}', '{}'])
def test_collect_filter_reports_invalid_existing_state(monkeypatch, tmp_path: Path, state_document: str) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    state_path = tmp_path / "data/state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(state_document, encoding="utf-8")
    monkeypatch.setattr(cli, "_root", lambda _root: tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _root: config)

    result = runner.invoke(app, ["collect-filter", "--output", str(tmp_path / "stage-1")])

    assert result.exit_code == 1
    assert "invalid data/state.json" in result.output


def test_cli_deep_read_removes_candidate_input_after_processing(tmp_path: Path) -> None:
    stage_one = tmp_path / "stage-1"
    output = tmp_path / "deep-reading-blog"
    stage_one.mkdir()
    candidates = [
        {
            "kind": "blog",
            "source_id": "example",
            "title": f"Feed Ranking {index}",
            "url": f"https://engineering.example.com/posts/{index}",
            "published_at": "2026-08-10T00:00:00Z",
            "authors": ["Example Engineer"],
            "excerpt": "Short excerpt",
        }
        for index in range(20)
    ]
    (stage_one / "blogs.jsonl").write_text("\n".join(json.dumps(item) for item in candidates), encoding="utf-8")

    class FakeServices(DeepReadServices):
        pass

    services = FakeServices(
        content=type("Content", (), {
            "feed_content": lambda _self, _candidate: "feed body",
            "fetch_article_html": None,
        })(),
        temporary_root=tmp_path / "temporary",
        text_reader=lambda *_args: {"system_context_zh": "context", "evidence_refs": [{"heading": "Architecture"}]},
        mineru=type("Parser", (), {"max_pdf_bytes": 100, "parse_pdf": lambda *_args: "paper"})(),
    )

    _run_deep_read("blog", stage_one, output, services, "run-1")

    assert not (output / "candidate-input").exists()
    payload = json.loads((output / "blog-deep-readings.json").read_text(encoding="utf-8"))
    assert len(payload["items"]) == 16
    assert payload["failures"] == []


def test_blog_services_do_not_require_mineru_key(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    class FakeText:
        pass

    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    monkeypatch.setattr(cli.TextClient, "from_config", lambda *_args, **_kwargs: FakeText())

    services = _real_services(config, Path(__file__).parents[2], tmp_path, kind="blog")

    assert services.mineru.max_pdf_bytes == config.models.mineru.max_pdf_bytes


def test_paper_services_require_mineru_key(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    monkeypatch.setattr(cli.TextClient, "from_config", lambda *_args, **_kwargs: object())

    with pytest.raises(ValueError, match="MinerU API key"):
        _real_services(config, Path(__file__).parents[2], tmp_path, kind="paper")


def test_real_services_threads_source_retry_timing_and_attempt_limiters(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli
    from datetime import UTC, datetime

    config = load_config(Path(__file__).parents[2])
    limits = config.settings.limits.model_copy(update={
        "arxiv_min_interval_seconds": 11,
        "blog_min_interval_seconds_per_domain": 13,
        "retry_backoff_seconds": 0.25,
        "retry_max_delay_seconds": 7,
    })
    config = config.model_copy(update={"settings": config.settings.model_copy(update={"limits": limits})})
    limiter_intervals: list[float] = []
    limiter_calls: list[tuple[float, str]] = []
    network_calls: list[tuple[str, str, dict[str, object]]] = []

    class FakeDomainLimiter:
        def __init__(self, interval: float) -> None:
            self.interval = interval
            limiter_intervals.append(interval)

        def acquire(self, url: str) -> None:
            limiter_calls.append((self.interval, url))

    class FakeClient:
        @classmethod
        def from_config(cls, *_args: object, **_kwargs: object) -> "FakeClient":
            return cls()

    def record(kind: str, result: object):
        def fetch(url_or_candidate: object, _limit: int, **kwargs: object) -> object:
            url = str(getattr(url_or_candidate, "url", url_or_candidate))
            network_calls.append((kind, url, kwargs))
            kwargs["attempt_limiter"]()  # type: ignore[operator]
            return result
        return fetch

    def fetch_feed(url: str, **kwargs: object) -> object:
        network_calls.append(("feed", url, kwargs))
        kwargs["attempt_limiter"]()  # type: ignore[operator]
        return SimpleNamespace(content=b"<rss version='2.0'><channel/></rss>")

    monkeypatch.setattr(cli, "DomainRateLimiter", FakeDomainLimiter)
    monkeypatch.setattr(cli, "TextClient", FakeClient)
    monkeypatch.setenv("MINERU_API_KEY", "test-key")
    monkeypatch.setattr(cli, "MinerUClient", lambda **kwargs: SimpleNamespace(max_pdf_bytes=kwargs["config"].max_pdf_bytes))
    monkeypatch.setattr(cli, "fetch_bytes_request", record("pdf", b"pdf"))
    monkeypatch.setattr(cli, "fetch_article_html_request", record("article", "<p>blog</p>"))
    monkeypatch.setattr(cli, "fetch_public_url", fetch_feed)

    services = _real_services(config, Path(__file__).parents[2], tmp_path, kind="paper")
    services.content.fetch_bytes("https://arxiv.org/pdf/2608.01234.pdf", 100)
    blog = config.sources.blogs[0]
    candidate = Candidate("blog", blog.id, "Post", blog.url, datetime(2026, 8, 10, tzinfo=UTC))
    assert services.content.fetch_article_html is not None
    services.content.fetch_article_html(candidate)
    assert services.blog_feed_content is not None
    services.blog_feed_content(candidate)

    assert limiter_intervals == [11, 13]
    assert [kind for kind, _url, _kwargs in network_calls] == ["pdf", "article", "feed"]
    for _kind, _url, kwargs in network_calls:
        assert kwargs["backoff_seconds"] == 0.25
        assert kwargs["max_delay_seconds"] == 7
        assert kwargs["max_attempts"] == limits.retry_attempts
    assert limiter_calls == [
        (11, "https://arxiv.org/pdf/2608.01234.pdf"),
        (13, blog.url),
        (13, blog.url),
    ]


def test_real_services_uses_kind_specific_deep_read_schema(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    calls: list[tuple[object, object]] = []

    class FakeText:
        def complete_json(self, messages: object, schema: object) -> dict[str, object]:
            calls.append((messages, schema))
            return {}

    monkeypatch.setattr(cli.TextClient, "from_config", lambda *args, **kwargs: FakeText())
    services = _real_services(config, Path(__file__).parents[2], tmp_path, kind="blog")
    services.text_reader("paper", "paper source", {"analysis_basis": "abstract_fallback"})
    services.text_reader("blog", "blog source", {"analysis_basis": "excerpt_fallback"})

    assert calls[0][1]["properties"]["analysis_basis"]["enum"] == ["mineru_full_text", "abstract_fallback"]
    assert calls[1][1]["properties"]["analysis_basis"]["enum"] == ["rss_full_content", "article_html", "excerpt_fallback"]
    assert calls[0][0][0]["role"] == "system"
    assert "source_documents" in json.loads(calls[0][0][1]["content"])
