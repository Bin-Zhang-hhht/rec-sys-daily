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
    assert set(jobs) == {"collect_filter", "deep_read", "similarity", "rank_integrate", "build_deploy"}
    assert jobs["collect_filter"]["timeout-minutes"] == 120
    assert jobs["deep_read"]["timeout-minutes"] == 300
    assert jobs["deep_read"]["strategy"]["matrix"]["kind"] == ["paper", "blog"]
    assert jobs["deep_read"]["strategy"]["max-parallel"] == 2
    assert jobs["similarity"]["timeout-minutes"] == 180
    assert jobs["rank_integrate"]["timeout-minutes"] == 120
    assert jobs["build_deploy"]["timeout-minutes"] == 60
    assert jobs["deep_read"]["needs"] == "collect_filter"
    assert jobs["similarity"]["needs"] == ["collect_filter", "deep_read"]
    assert jobs["rank_integrate"]["needs"] == ["collect_filter", "deep_read", "similarity"]
    assert jobs["build_deploy"]["needs"] == ["rank_integrate", "similarity"]
    assert jobs["build_deploy"]["permissions"]["pages"] == "write"
    assert jobs["build_deploy"]["permissions"]["id-token"] == "write"
    assert jobs["build_deploy"]["permissions"]["contents"] == "write"
    for job_name in ("collect_filter", "deep_read", "similarity", "rank_integrate"):
        assert jobs[job_name]["permissions"] == {"contents": "read"}
    assert jobs["collect_filter"]["steps"][0]["run"] == 'test "$GITHUB_REF" = "refs/heads/main"'
    deploy_steps = jobs["build_deploy"]["steps"]
    deploy_index = next(index for index, step in enumerate(deploy_steps) if step.get("uses") == "actions/deploy-pages@v4")
    promote_index = next(index for index, step in enumerate(deploy_steps) if step.get("name") == "Promote pending data after deployment")
    assert deploy_index < promote_index


def test_daily_workflow_is_scheduled_for_0012_bjt_and_has_artifact_boundaries() -> None:
    workflow = _load_workflow("daily.yml")
    trigger = workflow["on"]
    assert trigger["schedule"] == [{"cron": "12 16 * * *"}]
    assert "workflow_dispatch" in trigger
    assert workflow["concurrency"] == {"group": "recsys-daily", "cancel-in-progress": False}
    text = (ROOT / ".github" / "workflows" / "daily.yml").read_text(encoding="utf-8")
    assert "retention-days: 1" in text
    assert "name: similarity-${{ github.run_id }}" in text
    assert "retention-days: 3" in text
    assert "target: similarity" in text
    assert "actions/cache@v4" in text
    assert "continue-on-error: true" in text
    assert 'key: "fastembed|0.8.0|sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2|384"' in text
    assert "FASTEMBED_CACHE_PATH: /workspace/cache/fastembed" in text
    assert "--output /workspace/similarity-work/similarity" in text
    assert '-v "$PWD/similarity-work:/workspace/similarity-work"' in text
    assert '-v "$PWD/similarity-work/similarity:/workspace/similarity-work/similarity"' not in text
    assert "path: stages/similarity" in text
    assert "SIMILARITY_ARTIFACT_PATH: /workspace/similarity/similarity.json" in text
    assert '-v "$PWD/similarity:/workspace/similarity:ro"' in text
    assert "pending-data" in text
    assert "actions/deploy-pages@v4" in text
    assert "MINERU_API_KEY: ${{ secrets.MINERU_API_KEY }}" in text
    assert "-e MINERU_API_KEY" in text
    assert "DEEPSEEK_BASE_URL: ${{ secrets.DEEPSEEK_BASE_URL }}" in text
    assert "DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}" in text
    assert "--output /workspace/publish-work/publish-bundle" in text
    assert '-v "$PWD/publish-work:/workspace/publish-work"' in text
    assert '-v "$PWD/publish-work/publish-bundle:/workspace/publish-work/publish-bundle"' not in text
    assert "rsync --archive --delete publish-bundle/pending-data/ data/" in text
    assert "git push origin HEAD:main" in text
    assert "SITE_ORIGIN: ${{ vars.SITE_ORIGIN }}" in text
    assert "NVIDIA" not in text


def test_site_only_requires_a_same_run_artifact_pair_and_never_promotes_data() -> None:
    workflow = _load_workflow("site-only.yml")
    assert workflow["concurrency"] == {"group": "recsys-daily", "cancel-in-progress": False}
    assert workflow["permissions"]["contents"] == "read"
    assert "contents: write" not in (ROOT / ".github" / "workflows" / "site-only.yml").read_text(encoding="utf-8")
    text = (ROOT / ".github" / "workflows" / "site-only.yml").read_text(encoding="utf-8")
    assert "const bundleName = `publish-bundle-${run.id}`" in text
    assert "const similarityName = `similarity-${run.id}`" in text
    assert "if (bundle && similarity)" in text
    assert "bundle_artifact_name" in text
    assert "similarity_artifact_name" in text
    assert text.count("run-id: ${{ steps.artifacts.outputs.run_id }}") == 2
    assert "SIMILARITY_ARTIFACT_PATH: /workspace/similarity/similarity.json" in text
    assert '-v "$PWD/similarity:/workspace/similarity:ro"' in text
    assert "rsync " not in text
    assert "git push" not in text


def test_feishu_notify_workflow_is_independent_read_only_and_scheduled_for_0909_bjt() -> None:
    workflow = _load_workflow("feishu-notify.yml")
    assert workflow["on"] == {
        "schedule": [{"cron": "9 1 * * *"}],
        "workflow_dispatch": None,
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"] == {"group": "feishu-daily-notify", "cancel-in-progress": False}
    assert set(workflow["jobs"]) == {"notify"}
    job = workflow["jobs"]["notify"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 10
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["run"] == 'test "$GITHUB_REF" = "refs/heads/main"'
    text = (ROOT / ".github" / "workflows" / "feishu-notify.yml").read_text(encoding="utf-8")
    assert "FEISHU_WEBHOOK_URL: ${{ secrets.FEISHU_WEBHOOK_URL }}" in text
    assert "FEISHU_WEBHOOK_SECRET: ${{ secrets.FEISHU_WEBHOOK_SECRET }}" in text
    assert "SITE_ORIGIN: ${{ vars.SITE_ORIGIN }}" in text
    assert "PYTHONPATH=pipeline python -m recsys_daily.feishu_notify --root ." in text
    assert "contents: write" not in text
    assert "git push" not in text
    assert "deploy-pages" not in text


def test_verify_workflow_is_fixture_only_and_builds_both_images() -> None:
    workflow = _load_workflow("verify.yml")
    assert set(workflow["jobs"]) == {"pipeline", "site"}
    assert workflow["jobs"]["site"]["needs"] == "pipeline"
    text = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "pipeline/Dockerfile" in text
    assert "site/Dockerfile" in text
    assert "target: pipeline" in text
    assert "target: similarity" in text
    assert 'import fastembed, importlib.metadata' in text
    assert 'importlib.metadata.version("fastembed") == "0.8.0"' in text
    assert "test-fixtures" in text
    assert "pagefind/pagefind.js" in text
    assert "graph-manifest.json" in text
    assert "graph-index.json" in text
    assert "graph-shards/d0" in text
    assert "graph-shards/nodes" in text
    assert "graph-shards/adjacency" in text
    assert "graph.json" not in text
    assert "fixture-similarity-${{ github.run_id }}" in text
    assert "SIMILARITY_ARTIFACT_PATH: /workspace/similarity/similarity.json" in text
    assert "test:build-contract" in text
    assert "type=tmpfs,destination=/workspace/test-parent" in text
    assert "SITE_ORIGIN: https://example.invalid" in text
    assert "scripts/**" not in text
    assert text.count("retention-days: 1") == 2


def test_verify_workflow_uses_runtime_scenarios() -> None:
    dockerfile = (ROOT / "pipeline" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY fixtures" not in dockerfile
    text = (ROOT / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
    assert "fixtures/**" not in text
    assert "test-fixtures --case all --work /workspace/work/verify/publish-bundle" in text
    assert "path: work/verify/similarity" in text
    assert "mkdir -p work/verify/publish-bundle" not in text
    assert "mkdir -p work/verify/similarity" not in text


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
