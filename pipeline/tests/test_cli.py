from pathlib import Path
import json

from typer.testing import CliRunner

from recsys_daily.cli import _real_services, _run_deep_read, app
from recsys_daily.config import load_config
from recsys_daily.deep_read import DeepReadServices


runner = CliRunner()


def test_cli_exposes_stage_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("run", "collect-filter", "deep-read", "rank-integrate", "test-fixtures"):
        assert command in result.stdout


def test_fixture_success_writes_publish_bundle_without_network(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test-fixtures", "--case", "cold-start", "--work", str(tmp_path)])
    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "publish-bundle" / "manifest.json").exists()
    assert (tmp_path / "publish-bundle" / "pending-data" / "state.json").exists()


def test_fixture_failure_does_not_write_canonical_state(tmp_path: Path) -> None:
    result = runner.invoke(app, ["test-fixtures", "--case", "failures", "--work", str(tmp_path)])
    assert result.exit_code != 0
    assert not (tmp_path / "data" / "state.json").exists()


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
        vision_reader=lambda _paths: {},
    )

    _run_deep_read("blog", stage_one, output, services, "run-1")

    assert not (output / "candidate-input").exists()
    payload = json.loads((output / "blog-deep-readings.json").read_text(encoding="utf-8"))
    assert len(payload["items"]) == 16


def test_full_reading_clients_share_one_limiter(monkeypatch, tmp_path: Path) -> None:
    import recsys_daily.cli as cli

    config = load_config(Path(__file__).parents[2])
    seen: list[object] = []

    class FakeText:
        pass

    class FakeVision:
        pass

    def fake_text(*_args: object, **kwargs: object) -> FakeText:
        seen.append(kwargs["limiter"])
        return FakeText()

    def fake_vision(*_args: object, **kwargs: object) -> FakeVision:
        seen.append(kwargs["limiter"])
        return FakeVision()

    monkeypatch.setattr(cli.TextClient, "from_config", fake_text)
    monkeypatch.setattr(cli.VisionClient, "from_config", fake_vision)

    _real_services(config, Path(__file__).parents[2], tmp_path)

    assert len(seen) == 2
    assert seen[0] is seen[1]
