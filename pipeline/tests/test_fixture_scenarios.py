import json
from pathlib import Path

from recsys_daily.testing_fixtures import run_fixture_scenarios


def test_all_fixture_scenarios_generate_without_repository_assets(tmp_path: Path) -> None:
    results = run_fixture_scenarios(tmp_path, case="all", repository_root=Path(__file__).parents[2])
    assert set(results) == {"cold-start", "daily", "degraded", "failures", "site"}
    fixture_root = Path(__file__).parents[2] / "fixtures"
    assert not fixture_root.exists() or not any(path.is_file() for path in fixture_root.rglob("*"))
    assert all(result.generated_root.is_relative_to(tmp_path) for result in results.values())


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_daily_fixture_keeps_history(tmp_path: Path) -> None:
    daily = run_fixture_scenarios(tmp_path, case="daily", repository_root=Path(__file__).parents[2])["daily"]
    assert daily.pending_state["recommended_item_ids"][:1] == ["historical-paper"]
    assert daily.historical_item_count > 0


def test_degraded_fixture_marks_metadata_and_rejects_incomplete_recommendations(tmp_path: Path) -> None:
    result = run_fixture_scenarios(tmp_path, case="degraded", repository_root=Path(__file__).parents[2])["degraded"]
    stage_items = [
        *_jsonl(result.generated_root / "stage-1/papers.jsonl"),
        *_jsonl(result.generated_root / "stage-1/blogs.jsonl"),
    ]
    degraded_items = [item for item in stage_items if item["degraded"] is True]

    assert len(degraded_items) == result.stage_report["metadata_degraded_count"] == 8
    incomplete_ids = {
        str(item["id"])
        for item in degraded_items
        if not item["summary_zh"]
        or any(not item[field] for field in ("targets", "scenarios", "tasks", "methods"))
    }
    assert incomplete_ids

    canonical_items = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (result.publish_bundle / "pending-data/items").rglob("*.json")
    ]
    canonical_ids = {item["id"] for item in canonical_items}
    assert incomplete_ids.isdisjoint(canonical_ids)
    assert any(item["llm"]["degraded"] is True for item in canonical_items)


def test_degraded_fixture_exercises_source_and_content_fallbacks(tmp_path: Path) -> None:
    result = run_fixture_scenarios(tmp_path, case="degraded", repository_root=Path(__file__).parents[2])["degraded"]
    blog_readings = json.loads(
        (result.generated_root / "deep-reading-blog/blog-deep-readings.json").read_text(encoding="utf-8")
    )["items"]
    bases = {item["deep_reading"]["analysis_basis"] for item in blog_readings}

    assert any(source["success"] is False for source in result.stage_report["sources"])
    assert "second Feed failed" in result.stage_report["warnings"]
    assert {"article_html", "excerpt_fallback"}.issubset(bases)


def test_failure_fixture_does_not_promote_seed_state(tmp_path: Path) -> None:
    result = run_fixture_scenarios(tmp_path, case="failures", repository_root=Path(__file__).parents[2])["failures"]
    assert set(result.failure_injections) == {"collect", "deep-read", "rank", "site", "deploy"}
    for failure_point, evidence in result.failure_injections.items():
        assert evidence.failure_point == failure_point
        assert evidence.state_before == evidence.state_after
        assert evidence.state_path.read_bytes() == evidence.state_before
        assert evidence.promoted is False
