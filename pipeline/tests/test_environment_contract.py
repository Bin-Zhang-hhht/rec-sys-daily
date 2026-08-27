from pathlib import Path
import tomllib

import yaml


ROOT = Path(__file__).parents[2]


def test_secret_and_generated_paths_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for entry in (".env", "work/", "site/dist/", "__pycache__/"):
        assert entry in ignore


def test_example_contains_no_secret_value() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NVIDIA" not in example
    assert "DEEPSEEK_API_KEY=" in example
    assert "MINERU_API_KEY=" in example
    assert "Bearer " not in example


def test_fastembed_is_isolated_to_the_similarity_docker_target() -> None:
    project = tomllib.loads((ROOT / "pipeline" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    optional = project["project"]["optional-dependencies"]
    dockerfile = (ROOT / "pipeline" / "Dockerfile").read_text(encoding="utf-8")

    assert not any(dependency.lower().startswith("fastembed") for dependency in dependencies)
    assert optional["similarity"] == ["fastembed==0.8.0"]
    similarity_stage = dockerfile.index("FROM base AS similarity")
    pipeline_stage = dockerfile.index("FROM base AS pipeline")
    assert similarity_stage < pipeline_stage
    assert "libgomp1" in dockerfile
    assert 'pip install --no-cache-dir ".[similarity]"' in dockerfile
    assert dockerfile.rstrip().endswith("FROM base AS pipeline")


def test_compose_mounts_canonical_data_and_shared_work_for_complete_local_runs() -> None:
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    pipeline = compose["services"]["pipeline"]
    site = compose["services"]["site"]

    assert pipeline["build"]["target"] == "similarity"
    assert "./data:/workspace/data:ro" in pipeline["volumes"]
    assert "./work:/workspace/work" in pipeline["volumes"]
    assert site["environment"]["PUBLISH_BUNDLE_DIR"] == "/workspace/work/publish-bundle"
    assert site["environment"]["SIMILARITY_ARTIFACT_PATH"] == "${SIMILARITY_ARTIFACT_PATH:-/workspace/work/similarity/similarity.json}"
    assert "./work:/workspace/work:ro" in site["volumes"]
