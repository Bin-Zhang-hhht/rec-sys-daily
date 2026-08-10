from pathlib import Path

from recsys_daily.testing_fixtures import run_fixture_scenarios


def test_all_fixture_scenarios_generate_without_repository_assets(tmp_path: Path) -> None:
    results = run_fixture_scenarios(tmp_path, case="all", repository_root=Path(__file__).parents[2])
    assert set(results) == {"cold-start", "daily", "degraded", "failures", "site"}
    fixture_root = Path(__file__).parents[2] / "fixtures"
    assert not fixture_root.exists() or not any(fixture_root.rglob("*"))
    assert all(result.generated_root.is_relative_to(tmp_path) for result in results.values())


def test_daily_fixture_keeps_history_and_degraded_fixture_records_fallback(tmp_path: Path) -> None:
    daily = run_fixture_scenarios(tmp_path, case="daily", repository_root=Path(__file__).parents[2])["daily"]
    degraded = run_fixture_scenarios(tmp_path, case="degraded", repository_root=Path(__file__).parents[2])["degraded"]
    assert daily.pending_state["recommended_item_ids"][:1] == ["historical-paper"]
    assert daily.historical_item_count > 0
    assert degraded.stage_report["metadata_degraded_count"] > 0
    assert degraded.stage_report["warnings"]


def test_failure_fixture_does_not_promote_seed_state(tmp_path: Path) -> None:
    result = run_fixture_scenarios(tmp_path, case="failures", repository_root=Path(__file__).parents[2])["failures"]
    assert result.promoted_state == result.pending_state
    assert result.publish_bundle is None
