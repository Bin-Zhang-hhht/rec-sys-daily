from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from recsys_daily.config import load_config
from recsys_daily.integrate import StageInputs, integrate, load_digest


ROOT = Path(__file__).parents[2]
CONFIG = load_config(ROOT)
PUBLISHED_AT = datetime(2026, 8, 9, tzinfo=UTC)


def _paper(item_id: str, score: float) -> dict[str, object]:
    return {
        "kind": "paper",
        "id": item_id,
        "title": f"Paper {item_id}",
        "summary_zh": f"Summary for {item_id}",
        "source": "arxiv",
        "url": f"https://arxiv.org/abs/{item_id}",
        "published_at": PUBLISHED_AT.isoformat().replace("+00:00", "Z"),
        "authors": ["Author"],
        "targets": ["content"],
        "scenarios": ["text_feed"],
        "tasks": ["ranking"],
        "methods": ["two_tower"],
        "relevance_score": score,
        "deep_reading": {
            "analysis_basis": "pdf_text",
            "visual_analysis": {"status": "not_required"},
        },
    }


def _blog(item_id: str, score: float) -> dict[str, object]:
    return {
        "kind": "blog",
        "id": item_id,
        "title": f"Blog {item_id}",
        "summary_zh": f"Summary for {item_id}",
        "source": "meta_engineering",
        "url": f"https://example.com/{item_id}",
        "published_at": PUBLISHED_AT.isoformat().replace("+00:00", "Z"),
        "authors": ["Author"],
        "targets": ["content"],
        "scenarios": ["text_feed"],
        "tasks": ["ranking"],
        "methods": ["two_tower"],
        "relevance_score": score,
        "deep_reading": {
            "analysis_basis": "excerpt_fallback",
            "system_context_zh": "context",
        },
    }


def _write_stage(path: Path, run_id: str, kind: str, items: list[dict[str, object]]) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(json.dumps({"run_id": run_id, "schema_version": "1"}), encoding="utf-8")
    (path / "items.jsonl").write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return path


def fixture_stages(tmp_path: Path, *, paper_run_id: str = "run-1", blog_run_id: str | None = None) -> StageInputs:
    blog_run_id = blog_run_id or paper_run_id
    stage1 = tmp_path / "stage-1"
    stage1.mkdir()
    (stage1 / "manifest.json").write_text(json.dumps({"run_id": paper_run_id, "schema_version": "1"}), encoding="utf-8")
    _write_stage(tmp_path / "paper", paper_run_id, "paper", [_paper(f"paper-{i}", 1 - i / 20) for i in range(10)])
    _write_stage(tmp_path / "blog", blog_run_id, "blog", [_blog(f"blog-{i}", 1 - i / 20) for i in range(10)])
    return StageInputs(stage1=stage1, paper=tmp_path / "paper", blog=tmp_path / "blog")


def test_publish_bundle_allowlist(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    assert sorted(path.name for path in bundle.path.iterdir()) == ["manifest.json", "pending-data", "taxonomy.json"]


def test_mismatched_manifest_is_rejected_without_state(tmp_path: Path) -> None:
    stages = fixture_stages(tmp_path, paper_run_id="a", blog_run_id="b")
    with pytest.raises(ValueError, match="run_id"):
        integrate(stages, tmp_path / "bundle", CONFIG, state=None)
    assert not (tmp_path / "bundle" / "pending-data" / "state.json").exists()


def test_digest_references_ids_and_caps_each_kind(tmp_path: Path) -> None:
    bundle = integrate(fixture_stages(tmp_path), tmp_path / "bundle", CONFIG, state=None)
    digest = load_digest(bundle)
    assert len(digest.papers) <= 8
    assert len(digest.blogs) <= 8
    assert all(isinstance(entry.item_id, str) for entry in digest.papers + digest.blogs)
    item_paths = list((bundle.path / "pending-data" / "items").rglob("*.json"))
    item_ids = {json.loads(path.read_text(encoding="utf-8"))["id"] for path in item_paths}
    assert {entry.item_id for entry in digest.papers + digest.blogs} <= item_ids
