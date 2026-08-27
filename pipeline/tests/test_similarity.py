from datetime import datetime
import json
import math
from pathlib import Path
from typing import Sequence

import pytest

import recsys_daily.similarity as similarity_module
from recsys_daily.config import load_config
from recsys_daily.similarity import (
    SimilarityItem,
    _cosine_edges,
    load_similarity_items,
    run_similarity,
    serialize_similarity_item,
    validate_similarity_artifact,
)


ROOT = Path(__file__).parents[2]
CONFIG = load_config(ROOT)


class Tokenizer:
    def encode(self, value: str) -> list[int]:
        return [ord(character) for character in value]

    def decode(self, value: Sequence[int]) -> str:
        return "".join(chr(token) for token in value)


class Embedder:
    tokenizer = Tokenizer()

    def __init__(self, vectors: Sequence[Sequence[float]] | None = None) -> None:
        self.vectors = vectors
        self.values: list[str] = []
        self.batch_size: int | None = None
        self.called = False

    def embed(self, values: Sequence[str], *, batch_size: int) -> Sequence[Sequence[float]]:
        self.called = True
        self.values = list(values)
        self.batch_size = batch_size
        if self.vectors is not None:
            return self.vectors
        return [[1.0] + [0.0] * 383 for _ in values]


def item(item_id: str, *, kind: str = "paper") -> SimilarityItem:
    return SimilarityItem(item_id, kind, "Title", "Abstract", "中文摘要")


def _write_manifest(path: Path, run_id: str = "run-1") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps({"run_id": run_id, "schema_version": "1"}),
        encoding="utf-8",
    )


def _current_item(
    item_id: str,
    kind: str,
    *,
    summary_zh: str,
    excerpt: str,
    system_context_zh: str | None = None,
    degraded: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {
        "id": item_id,
        "kind": kind,
        "title": f"Current {item_id}",
        "summary_zh": summary_zh,
        "source_id": "arxiv" if kind == "paper" else "meta_engineering",
        "url": f"https://example.com/{item_id}",
        "published_at": "2026-08-01T00:00:00Z",
        "authors": ["Author"],
        "targets": [CONFIG.topics.targets[0].id],
        "scenarios": [CONFIG.topics.scenarios[0].id],
        "tasks": [CONFIG.topics.tasks[0].id],
        "methods": [CONFIG.topics.methods[0].id],
        "relevance_score": 0.8,
        "degraded": degraded,
        "excerpt": excerpt,
        "deep_reading": {
            "analysis_basis": "abstract_fallback" if kind == "paper" else "excerpt_fallback",
        },
    }
    if kind == "paper":
        value.update({"arxiv_id": "2608.00001", "doi": None})
    elif system_context_zh is not None:
        value["deep_reading"]["system_context_zh"] = system_context_zh
    return value


def _write_stage_inputs(
    root: Path,
    *,
    papers: list[dict[str, object]] | None = None,
    blogs: list[dict[str, object]] | None = None,
    run_id: str = "run-1",
) -> tuple[Path, Path, Path]:
    papers = papers or []
    blogs = blogs or []
    stage1 = root / "stage-1"
    paper = root / "deep-reading-paper"
    blog = root / "deep-reading-blog"
    for path in (stage1, paper, blog):
        _write_manifest(path, run_id)
    for kind, values in (("paper", papers), ("blog", blogs)):
        records = [{key: value for key, value in entry.items() if key != "deep_reading"} for entry in values]
        (stage1 / f"{kind}s.jsonl").write_text(
            "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in records),
            encoding="utf-8",
        )
        readings = [
            {"id": value["id"], "kind": kind, "deep_reading": value["deep_reading"]}
            for value in values
        ]
        target = paper if kind == "paper" else blog
        (target / f"{kind}-deep-readings.json").write_text(
            json.dumps({"kind": kind, "items": readings, "failures": []}, ensure_ascii=False),
            encoding="utf-8",
        )
    return stage1, paper, blog


def _canonical_base(item_id: str, kind: str, published_at: str) -> dict[str, object]:
    return {
        "id": item_id,
        "kind": kind,
        "title": f"Historical {item_id}",
        "summary_zh": f"{item_id} 的中文摘要",
        "source": "arxiv" if kind == "paper" else "meta_engineering",
        "url": f"https://example.com/{item_id}",
        "published_at": published_at,
        "authors": ["Author"],
        "targets": [CONFIG.topics.targets[0].id],
        "scenarios": [CONFIG.topics.scenarios[0].id],
        "tasks": [CONFIG.topics.tasks[0].id],
        "methods": [CONFIG.topics.methods[0].id],
        "relevance_score": 0.8,
        "final_score": 0.8,
    }


def _write_historical_item(data: Path, value: dict[str, object]) -> None:
    published = datetime.fromisoformat(str(value["published_at"]).replace("Z", "+00:00"))
    kind_dir = "papers" if value["kind"] == "paper" else "blogs"
    path = data / "items" / kind_dir / f"{published.year:04d}" / f"{published.month:02d}" / f"{value['id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _artifact_document(
    item_ids: set[str],
    *,
    edges: list[dict[str, object]] | None = None,
    run_id: str = "run-1",
) -> dict[str, object]:
    settings = CONFIG.settings.similarity
    return {
        "run_id": run_id,
        "schema_version": "1",
        "model": {
            "library": settings.library,
            "version": settings.version,
            "name": settings.model,
            "dimension": settings.dimension,
            "normalized": True,
        },
        "parameters": {
            "max_input_tokens": settings.max_input_tokens,
            "title_tokens": settings.title_tokens,
            "abstract_tokens": settings.abstract_tokens,
            "summary_tokens": settings.summary_tokens,
            "separator_tokens": settings.separator_tokens,
            "top_k": settings.top_k,
            "min_cosine": settings.min_cosine,
            "mutual_top_k": settings.mutual_top_k,
        },
        "items_considered": len(item_ids),
        "encoded_items": len(item_ids),
        "token_counts": [
            {"id": item_id, "title": 1, "abstract": 1, "summary_zh": 1, "total": 4}
            for item_id in sorted(item_ids)
        ],
        "edges": edges or [],
    }


def _write_artifact(path: Path, document: dict[str, object], *, run_id: str = "run-1") -> Path:
    _write_manifest(path, run_id)
    destination = path / "similarity.json"
    destination.write_text(json.dumps(document, allow_nan=True), encoding="utf-8")
    report = {
        "run_id": run_id,
        "schema_version": "1",
        "model": document["model"],
        "items_considered": document["items_considered"],
        "encoded_items": document["encoded_items"],
        "truncated_items": 0,
        "edge_count": len(document["edges"]),
        "top_k": CONFIG.settings.similarity.top_k,
        "min_cosine": CONFIG.settings.similarity.min_cosine,
        "elapsed_seconds": 0.0,
        "cache_status": "not_observed",
    }
    (path / "similarity-report.json").write_text(json.dumps(report), encoding="utf-8")
    return destination


def _validate(path: Path, item_ids: set[str], *, run_id: str = "run-1") -> None:
    validate_similarity_artifact(
        path,
        expected_item_ids=item_ids,
        config=CONFIG.settings.similarity,
        run_id=run_id,
        schema_version="1",
    )


def test_serializer_uses_only_three_roles_and_hard_token_budget() -> None:
    text, counts = serialize_similarity_item(
        SimilarityItem("paper-a", "paper", "t" * 100, "a" * 100, "中" * 100),
        Tokenizer(),
        CONFIG.settings.similarity,
    )
    assert text == f"T:{'t' * 32}\nA:{'a' * 64}\nS:{'中' * 24}"
    assert counts.title == 32
    assert counts.abstract == 64
    assert counts.summary_zh == 24
    assert counts.total == 128


def test_cosine_edges_are_exact_mutual_top_k_and_stably_ordered() -> None:
    values = [item("a"), item("b"), item("c")]
    vectors = [
        (1.0, 0.0),
        (0.99 / (0.99**2 + 0.1**2) ** 0.5, 0.1 / (0.99**2 + 0.1**2) ** 0.5),
        (0.0, 1.0),
    ]
    edges = _cosine_edges(
        values,
        vectors,
        CONFIG.settings.similarity.model_copy(update={"min_cosine": 0.5, "top_k": 1}),
    )
    assert [(edge.source_id, edge.target_id) for edge in edges] == [("a", "b")]
    assert edges[0].score == 0.994937
    assert edges[0].source_rank == edges[0].target_rank == 1


def test_load_similarity_items_merges_current_and_history_with_approved_roles(tmp_path: Path) -> None:
    current_paper = _current_item(
        "shared-paper", "paper", summary_zh="当前论文摘要", excerpt="current paper abstract"
    )
    current_blog = _current_item(
        "current-blog",
        "blog",
        summary_zh="当前博客摘要",
        excerpt="must not enter similarity",
        system_context_zh="当前博客系统上下文",
    )
    stage1, paper, blog = _write_stage_inputs(tmp_path, papers=[current_paper], blogs=[current_blog])
    data = tmp_path / "data"
    historical_paper = _canonical_base("historical-paper", "paper", "2025-01-02T00:00:00Z")
    historical_paper.update({
        "abstract": "historical paper abstract",
        "arxiv_id": "2501.00001",
        "doi": None,
        "deep_reading": {"analysis_basis": "abstract_fallback"},
    })
    historical_blog = _canonical_base("historical-blog", "blog", "2025-02-02T00:00:00Z")
    historical_blog["deep_reading"] = {
        "analysis_basis": "article_html",
        "system_context_zh": "历史博客系统上下文",
    }
    stale_shared = _canonical_base("shared-paper", "paper", "2025-03-02T00:00:00Z")
    stale_shared.update({
        "abstract": "stale historical abstract",
        "arxiv_id": "2503.00001",
        "doi": None,
        "deep_reading": {"analysis_basis": "abstract_fallback"},
    })
    for value in (historical_paper, historical_blog, stale_shared):
        _write_historical_item(data, value)

    values = load_similarity_items(stage1, paper, blog, data, CONFIG)
    assert [value.id for value in values] == ["current-blog", "historical-blog", "historical-paper", "shared-paper"]
    by_id = {value.id: value for value in values}
    assert by_id["shared-paper"].title == "Current shared-paper"
    assert by_id["shared-paper"].abstract == "current paper abstract"
    assert by_id["historical-paper"].abstract == "historical paper abstract"
    assert by_id["current-blog"].abstract == "当前博客系统上下文"
    assert by_id["historical-blog"].abstract == "历史博客系统上下文"
    assert "must not enter similarity" not in {value.abstract for value in values}


def test_load_similarity_items_excludes_degraded_non_chinese_success(tmp_path: Path) -> None:
    degraded = _current_item(
        "paper-a",
        "paper",
        summary_zh="English only summary",
        excerpt="paper abstract",
        degraded=True,
    )
    stage1, paper, blog = _write_stage_inputs(tmp_path, papers=[degraded])
    assert load_similarity_items(stage1, paper, blog, None, CONFIG) == []


def test_load_similarity_items_accepts_degraded_item_with_one_grounded_label(tmp_path: Path) -> None:
    degraded = _current_item(
        "paper-a",
        "paper",
        summary_zh="可展示的中文降级摘要",
        excerpt="paper abstract",
        degraded=True,
    )
    degraded["scenarios"] = []
    degraded["tasks"] = []
    degraded["methods"] = []
    stage1, paper, blog = _write_stage_inputs(tmp_path, papers=[degraded])

    assert [item.id for item in load_similarity_items(stage1, paper, blog, None, CONFIG)] == ["paper-a"]


def test_run_similarity_accepts_empty_input_and_publishes_only_declared_files(tmp_path: Path) -> None:
    stage1, paper, blog = _write_stage_inputs(tmp_path)
    output = tmp_path / "similarity"
    embedder = Embedder()

    destination = run_similarity(stage1, paper, blog, None, output, CONFIG, embedder=embedder)

    assert destination == output / "similarity.json"
    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "similarity-report.json",
        "similarity.json",
    }
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["items_considered"] == document["encoded_items"] == 0
    assert document["token_counts"] == document["edges"] == []
    assert embedder.called is False
    report = json.loads((output / "similarity-report.json").read_text(encoding="utf-8"))
    assert report["cache_status"] == "not_used"
    assert report["truncated_items"] == report["edge_count"] == 0
    assert not ({"id", "text", "embedding", "candidate_text"} & set(report))
    _validate(destination, set())


def test_run_similarity_does_not_initialize_fastembed_for_empty_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage1, paper, blog = _write_stage_inputs(tmp_path)

    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        raise AssertionError("FastEmbed must not be initialized for an empty corpus")

    monkeypatch.setattr(similarity_module, "_FastEmbedder", fail_if_initialized)
    destination = run_similarity(stage1, paper, blog, None, tmp_path / "similarity", CONFIG)
    assert destination.exists()


def test_run_similarity_rejects_existing_output_without_mutation(tmp_path: Path) -> None:
    stage1, paper, blog = _write_stage_inputs(tmp_path)
    output = tmp_path / "similarity"
    output.mkdir()
    sentinel = output / "undeclared.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        run_similarity(stage1, paper, blog, None, output, CONFIG, embedder=Embedder())

    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert {path.name for path in output.iterdir()} == {"undeclared.txt"}


def test_run_similarity_cleans_temporary_output_after_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage1, paper, blog = _write_stage_inputs(tmp_path)
    output = tmp_path / "similarity"
    original_write_json = similarity_module.write_json

    def failing_write(path: Path, value: object) -> None:
        if path.name == "manifest.json":
            raise OSError("injected write failure")
        original_write_json(path, value)

    monkeypatch.setattr(similarity_module, "write_json", failing_write)
    with pytest.raises(OSError, match="injected write failure"):
        run_similarity(stage1, paper, blog, None, output, CONFIG, embedder=Embedder())

    assert not output.exists()
    assert list(tmp_path.glob(".similarity-*")) == []


@pytest.mark.parametrize(
    ("vectors", "message"),
    [
        ([], "embedding count"),
        ([[1.0, 0.0]], "embedding dimension"),
    ],
)
def test_run_similarity_rejects_embedding_count_or_dimension(
    tmp_path: Path,
    vectors: Sequence[Sequence[float]],
    message: str,
) -> None:
    current = _current_item(
        "paper-a", "paper", summary_zh="论文摘要", excerpt="paper abstract"
    )
    stage1, paper, blog = _write_stage_inputs(tmp_path, papers=[current])
    output = tmp_path / "similarity"

    with pytest.raises(ValueError, match=message):
        run_similarity(stage1, paper, blog, None, output, CONFIG, embedder=Embedder(vectors))

    assert not output.exists()


def test_validate_similarity_artifact_accepts_stable_mutual_edges(tmp_path: Path) -> None:
    item_ids = {"a", "b", "c"}
    edges = [
        {"source_id": "a", "target_id": "b", "score": 0.9, "source_rank": 1, "target_rank": 1},
        {"source_id": "a", "target_id": "c", "score": 0.8, "source_rank": 2, "target_rank": 1},
    ]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    _validate(path, item_ids)


@pytest.mark.parametrize(
    ("edges", "message"),
    [
        ([{"source_id": "a", "target_id": "unknown", "score": 0.8, "source_rank": 1, "target_rank": 1}], "endpoint is invalid"),
        ([{"source_id": "a", "target_id": "a", "score": 0.8, "source_rank": 1, "target_rank": 1}], "self-loop"),
        ([{"source_id": "b", "target_id": "a", "score": 0.8, "source_rank": 1, "target_rank": 1}], "stable ID order"),
        (
            [
                {"source_id": "a", "target_id": "b", "score": 0.8, "source_rank": 1, "target_rank": 1},
                {"source_id": "a", "target_id": "b", "score": 0.8, "source_rank": 2, "target_rank": 2},
            ],
            "duplicate similarity edge",
        ),
        ([{"source_id": "a", "target_id": "b", "score": 0.8, "source_rank": 6, "target_rank": 1}], "Top-K"),
        ([{"source_id": "a", "target_id": "b", "score": 0.7, "source_rank": 1, "target_rank": 1}], "threshold"),
        ([{"source_id": "a", "target_id": "b", "score": 0.8000001, "source_rank": 1, "target_rank": 1}], "stably rounded"),
    ],
)
def test_validate_similarity_artifact_rejects_invalid_edge_contract(
    tmp_path: Path,
    edges: list[dict[str, object]],
    message: str,
) -> None:
    item_ids = {"a", "b"}
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError, match=message):
        _validate(path, item_ids)


@pytest.mark.parametrize("rank", [0, "1", 1.5])
def test_validate_similarity_artifact_rejects_non_positive_or_non_integer_rank(
    tmp_path: Path,
    rank: object,
) -> None:
    item_ids = {"a", "b"}
    edges = [{"source_id": "a", "target_id": "b", "score": 0.8, "source_rank": rank, "target_rank": 1}]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_non_finite_score(tmp_path: Path) -> None:
    item_ids = {"a", "b"}
    edges = [{"source_id": "a", "target_id": "b", "score": math.nan, "source_rank": 1, "target_rank": 1}]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_unstable_token_or_edge_order(tmp_path: Path) -> None:
    item_ids = {"a", "b", "c"}
    token_document = _artifact_document(item_ids)
    token_document["token_counts"] = list(reversed(token_document["token_counts"]))
    token_path = _write_artifact(tmp_path / "token-order", token_document)
    with pytest.raises(ValueError, match="token counts"):
        _validate(token_path, item_ids)

    edges = [
        {"source_id": "a", "target_id": "c", "score": 0.8, "source_rank": 1, "target_rank": 1},
        {"source_id": "a", "target_id": "b", "score": 0.9, "source_rank": 2, "target_rank": 1},
    ]
    edge_path = _write_artifact(tmp_path / "edge-order", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError, match="stable endpoint order"):
        _validate(edge_path, item_ids)


def test_validate_similarity_artifact_rejects_duplicate_endpoint_rank(tmp_path: Path) -> None:
    item_ids = {"a", "b", "c"}
    edges = [
        {"source_id": "a", "target_id": "b", "score": 0.9, "source_rank": 1, "target_rank": 1},
        {"source_id": "a", "target_id": "c", "score": 0.8, "source_rank": 1, "target_rank": 1},
    ]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError, match="ranks must be unique"):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_rank_score_inversion(tmp_path: Path) -> None:
    item_ids = {"a", "b", "c"}
    edges = [
        {"source_id": "a", "target_id": "b", "score": 0.9, "source_rank": 2, "target_rank": 1},
        {"source_id": "a", "target_id": "c", "score": 0.8, "source_rank": 1, "target_rank": 1},
    ]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))

    with pytest.raises(ValueError, match="rank/score order"):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_endpoint_degree_above_top_k(tmp_path: Path) -> None:
    item_ids = {"a", "b", "c", "d", "e", "f", "g"}
    edges = [
        {
            "source_id": "a",
            "target_id": target,
            "score": round(0.9 - index / 100, 6),
            "source_rank": min(index, 5),
            "target_rank": 1,
        }
        for index, target in enumerate(sorted(item_ids - {"a"}), 1)
    ]
    path = _write_artifact(tmp_path / "similarity", _artifact_document(item_ids, edges=edges))
    with pytest.raises(ValueError, match="degree exceeds Top-K"):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_undeclared_files(tmp_path: Path) -> None:
    item_ids = {"a"}
    directory = tmp_path / "similarity"
    path = _write_artifact(directory, _artifact_document(item_ids))
    (directory / "candidate-text.txt").write_text("must not cross jobs", encoding="utf-8")
    with pytest.raises(ValueError, match="undeclared files"):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_mismatched_report(tmp_path: Path) -> None:
    item_ids = {"a"}
    directory = tmp_path / "similarity"
    path = _write_artifact(directory, _artifact_document(item_ids))
    report_path = directory / "similarity-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["encoded_items"] = 0
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="report does not match"):
        _validate(path, item_ids)


@pytest.mark.parametrize("field", ["items_considered", "encoded_items"])
def test_validate_similarity_artifact_rejects_input_count_mismatch(tmp_path: Path, field: str) -> None:
    item_ids = {"a"}
    document = _artifact_document(item_ids)
    document[field] = 2
    path = _write_artifact(tmp_path / "similarity", document)
    with pytest.raises(ValueError, match="full canonical input"):
        _validate(path, item_ids)


def test_validate_similarity_artifact_rejects_wrong_model_dimension(tmp_path: Path) -> None:
    item_ids = {"a"}
    document = _artifact_document(item_ids)
    document["model"]["dimension"] = 383
    path = _write_artifact(tmp_path / "similarity", document)
    with pytest.raises(ValueError):
        _validate(path, item_ids)


def test_similarity_artifact_rejects_text_payload(tmp_path: Path) -> None:
    item_ids = {"a"}
    document = _artifact_document(item_ids)
    document["candidate_text"] = "must not be accepted"
    path = _write_artifact(tmp_path / "similarity", document)
    with pytest.raises(ValueError):
        _validate(path, item_ids)
