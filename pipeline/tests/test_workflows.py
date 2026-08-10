from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _load_workflow(name: str) -> dict:
    value = yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_daily_workflow_permissions_timeouts_and_needs() -> None:
    workflow = _load_workflow("daily.yml")
    jobs = workflow["jobs"]
    assert jobs["collect_filter"]["timeout-minutes"] == 120
    assert jobs["deep_read"]["timeout-minutes"] == 300
    assert jobs["deep_read"]["strategy"]["matrix"]["kind"] == ["paper", "blog"]
    assert jobs["deep_read"]["strategy"]["max-parallel"] == 2
    assert jobs["rank_integrate"]["timeout-minutes"] == 120
    assert jobs["build_deploy"]["timeout-minutes"] == 60
    assert jobs["deep_read"]["needs"] == "collect_filter"
    assert jobs["rank_integrate"]["needs"] == ["collect_filter", "deep_read"]
    assert jobs["build_deploy"]["needs"] == "rank_integrate"
    assert jobs["build_deploy"]["permissions"]["pages"] == "write"
    assert jobs["build_deploy"]["permissions"]["id-token"] == "write"
    assert jobs["build_deploy"]["permissions"]["contents"] == "write"
    assert "pages" not in jobs["collect_filter"].get("permissions", {})
    assert "contents" not in jobs["collect_filter"]["permissions"] or jobs["collect_filter"]["permissions"]["contents"] == "read"


def test_daily_workflow_has_single_schedule_and_artifact_boundaries() -> None:
    workflow = _load_workflow("daily.yml")
    trigger = workflow["on"]
    assert trigger["schedule"] == [{"cron": "23 0 * * *"}]
    assert "workflow_dispatch" in trigger
    assert workflow["concurrency"] == {"group": "recsys-daily", "cancel-in-progress": False}
    text = (ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert "retention-days: 1" in text
    assert "pending-data" in text
    assert "actions/deploy-pages@v4" in text


def test_verify_workflow_is_fixture_only_and_builds_both_images() -> None:
    workflow = _load_workflow("verify.yml")
    assert set(workflow["jobs"]) == {"pipeline", "site"}
    assert workflow["jobs"]["site"]["needs"] == "pipeline"
    text = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "pipeline/Dockerfile" in text
    assert "site/Dockerfile" in text
    assert "test-fixtures" in text
    assert "pagefind/pagefind.js" in text
    assert "graph.json" in text
    assert "retention-days: 1" not in text


def test_verify_workflow_uses_runtime_scenarios() -> None:
    dockerfile = (ROOT / "pipeline" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY fixtures" not in dockerfile
    text = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "fixtures/**" not in text
    assert "test-fixtures --case all" in text


def test_site_docker_context_excludes_host_dependencies_and_build_outputs() -> None:
    pipeline_dockerfile = (ROOT / "pipeline" / "Dockerfile").read_text(encoding="utf-8")
    path = ROOT / "site" / ".dockerignore"
    ignored = {
        line.strip()
        for line in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "COPY site/.dockerignore /workspace/site/.dockerignore" in pipeline_dockerfile
    assert {"node_modules", ".astro", "dist"} <= ignored
