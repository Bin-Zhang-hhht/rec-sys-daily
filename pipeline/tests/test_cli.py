from pathlib import Path

from typer.testing import CliRunner

from recsys_daily.cli import app


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
