"""Offline semantic similarity calculation for the short-lived workflow artifact."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any, Iterable, Protocol, Sequence

from .artifacts import read_json, read_jsonl, write_json
from .config import AppConfig, SimilarityConfig
from .metadata import has_cjk
from .schemas import (
    BlogItem,
    PaperItem,
    SimilarityArtifact,
    SimilarityEdge,
    SimilarityModel,
    SimilarityParameters,
    SimilarityReport,
    SimilarityTokenCounts,
    Stage1Metadata,
)


class Tokenizer(Protocol):
    def encode(self, value: str) -> Sequence[int]: ...

    def decode(self, value: Sequence[int]) -> str: ...


class Embedder(Protocol):
    def embed(self, values: Sequence[str], *, batch_size: int) -> Iterable[Sequence[float]]: ...


@dataclass(frozen=True)
class SimilarityItem:
    id: str
    kind: str
    title: str
    abstract: str
    summary_zh: str


class _FastEmbedTokenizer:
    def __init__(self, tokenizer: Any) -> None:
        self._tokenizer = tokenizer

    def encode(self, value: str) -> Sequence[int]:
        return self._tokenizer.encode(value).ids

    def decode(self, value: Sequence[int]) -> str:
        return self._tokenizer.decode(list(value), skip_special_tokens=True)


class _FastEmbedder:
    def __init__(self, config: SimilarityConfig) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(
            model_name=config.model,
            threads=config.threads,
        )
        tokenizer = getattr(getattr(self._model, "model", None), "tokenizer", None)
        if tokenizer is None:
            raise RuntimeError("fastembed did not expose the model tokenizer")
        self.tokenizer: Tokenizer = _FastEmbedTokenizer(tokenizer)

    def embed(self, values: Sequence[str], *, batch_size: int) -> Iterable[Sequence[float]]:
        yield from self._model.embed(values, batch_size=batch_size)


def _manifest(path: Path) -> dict[str, Any]:
    value = read_json(path / "manifest.json")
    if set(value) != {"run_id", "schema_version"}:
        raise ValueError(f"stage manifest contains undeclared fields: {path}")
    if not isinstance(value.get("run_id"), str) or not value["run_id"].strip():
        raise ValueError(f"invalid stage manifest: {path}")
    if value.get("schema_version") != "1":
        raise ValueError(f"unsupported stage manifest schema: {path}")
    return value


def _successful_ids(path: Path, kind: str) -> set[str]:
    source = path / f"{kind}-deep-readings.json"
    document = read_json(source)
    if document.get("kind") != kind or not isinstance(document.get("items"), list):
        raise ValueError(f"invalid {kind} deep-reading artifact: {source}")
    values = document["items"]
    ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
            raise ValueError(f"invalid {kind} deep-reading item id: {source}")
        if value["id"] in ids:
            raise ValueError(f"duplicate {kind} deep-reading item id: {value['id']}")
        ids.add(value["id"])
    return ids


def _validated_stage1_metadata(value: dict[str, Any], config: AppConfig) -> Stage1Metadata:
    fields = (
        "id", "summary_zh", "targets", "scenarios", "tasks", "methods",
        "relevance_score", "degraded",
    )
    missing = [field for field in fields if field not in value]
    if missing:
        raise ValueError(
            f"stage-1 metadata is incomplete for {value.get('id', '<unknown>')}: {', '.join(missing)}"
        )
    metadata = Stage1Metadata.model_validate({field: value[field] for field in fields})
    for category in ("targets", "scenarios", "tasks", "methods"):
        allowed = {entry.id for entry in getattr(config.topics, category)}
        unknown = sorted(set(getattr(metadata, category)) - allowed)
        if unknown:
            raise ValueError(f"unknown {category} id: {unknown[0]}")
    if not metadata.summary_zh or not metadata.summary_zh.strip() or not has_cjk(metadata.summary_zh):
        raise ValueError(f"stage-1 metadata has no displayable Chinese summary: {metadata.id}")
    return metadata


def _is_publishable_degraded_metadata(value: dict[str, Any]) -> bool:
    if value.get("degraded") is not True:
        return True
    summary = value.get("summary_zh")
    return bool(isinstance(summary, str) and has_cjk(summary)) and any(
        isinstance(value.get(field), list) and bool(value[field])
        for field in ("targets", "scenarios", "tasks", "methods")
    )


def _current_canonical_item(
    value: dict[str, Any],
    reading_value: dict[str, Any],
    kind: str,
    config: AppConfig,
) -> PaperItem | BlogItem:
    item_id = str(value.get("id", ""))
    metadata = _validated_stage1_metadata(value, config)
    base = dict(value)
    base.update(reading_value)
    for field in ("summary_zh", "targets", "scenarios", "tasks", "methods", "relevance_score"):
        base[field] = getattr(metadata, field)
    if "source" not in base and base.get("source_id"):
        base["source"] = base["source_id"]
    for key in (
        "source_id", "source_entry_id", "categories", "source_weight", "source_scenarios",
        "metadata_score", "degraded",
    ):
        base.pop(key, None)
    if kind == "paper":
        base["abstract"] = base.pop("excerpt", "")
        item_type = PaperItem
    else:
        excerpt = base.get("excerpt")
        if excerpt is not None and (
            not isinstance(excerpt, str)
            or len(excerpt) > config.settings.storage.max_blog_excerpt_chars
        ):
            raise ValueError(f"stage-1 blog excerpt is invalid: {item_id}")
        base.pop("excerpt", None)
        base.pop("arxiv_id", None)
        base.pop("doi", None)
        item_type = BlogItem
    try:
        return item_type.model_validate(base, context={"taxonomy": config.topics})
    except Exception as exc:
        raise ValueError(f"current canonical similarity input is invalid: {item_id}") from exc


def _current_items(stage1: Path, paper: Path, blog: Path, config: AppConfig) -> list[SimilarityItem]:
    metadata: dict[str, dict[str, Any]] = {}
    for kind in ("paper", "blog"):
        for value in read_jsonl(stage1 / f"{kind}s.jsonl"):
            item_id = value.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("stage-1 candidate id is required for similarity")
            if item_id in metadata:
                raise ValueError(f"duplicate stage-1 candidate id: {item_id}")
            metadata[item_id] = value

    result: list[SimilarityItem] = []
    for kind, directory in (("paper", paper), ("blog", blog)):
        successful = _successful_ids(directory, kind)
        document = read_json(directory / f"{kind}-deep-readings.json")
        readings = {value["id"]: value for value in document["items"]}
        for item_id in sorted(successful):
            value = metadata.get(item_id)
            if value is None:
                raise ValueError(f"deep-reading item is absent from stage-1: {item_id}")
            if value.get("kind") != kind:
                raise ValueError(f"deep-reading kind does not match stage-1 candidate: {item_id}")
            reading_value = readings.get(item_id)
            if not isinstance(reading_value, dict) or reading_value.get("kind") != kind:
                raise ValueError(f"deep-reading kind is invalid: {item_id}")
            reading = reading_value.get("deep_reading")
            if not isinstance(reading, dict):
                raise ValueError(f"deep-reading content is missing: {item_id}")
            if not _is_publishable_degraded_metadata(value):
                continue
            canonical = _current_canonical_item(value, reading_value, kind, config)
            abstract = canonical.abstract if canonical.kind == "paper" else (canonical.deep_reading.system_context_zh or "")
            result.append(SimilarityItem(canonical.id, canonical.kind, canonical.title, abstract, canonical.summary_zh))
    return result


def _historical_items(repository_data: Path | None, config: AppConfig) -> list[SimilarityItem]:
    if repository_data is None or not repository_data.exists():
        return []
    result: list[SimilarityItem] = []
    for source in sorted((repository_data / "items").rglob("*.json")):
        relative = source.relative_to(repository_data)
        parts = relative.parts
        if (
            len(parts) != 5
            or parts[0] != "items"
            or parts[1] not in {"papers", "blogs"}
            or source.suffix != ".json"
        ):
            raise ValueError(f"unsupported historical canonical item path: {relative.as_posix()}")
        value = read_json(source)
        kind_dir = parts[1]
        item_type = PaperItem if kind_dir == "papers" else BlogItem
        item = item_type.model_validate(value, context={"taxonomy": config.topics})
        expected_kind = "paper" if kind_dir == "papers" else "blog"
        if item.kind != expected_kind or item.id != source.stem:
            raise ValueError(f"historical canonical item does not match its path: {relative.as_posix()}")
        if (f"{item.published_at.year:04d}", f"{item.published_at.month:02d}") != parts[2:4]:
            raise ValueError(f"historical canonical item date does not match its path: {relative.as_posix()}")
        abstract = item.abstract if item.kind == "paper" else (item.deep_reading.system_context_zh or "")
        result.append(SimilarityItem(item.id, item.kind, item.title, abstract, item.summary_zh))
    return result


def load_similarity_items(
    stage1: Path,
    paper: Path,
    blog: Path,
    repository_data: Path | None,
    config: AppConfig,
) -> list[SimilarityItem]:
    """Merge successful current items with every historical canonical item by stable ID."""
    values = {item.id: item for item in _historical_items(repository_data, config)}
    values.update({item.id: item for item in _current_items(stage1, paper, blog, config)})
    return [values[item_id] for item_id in sorted(values)]


def _role_tokens(tokenizer: Tokenizer, value: str, limit: int) -> tuple[str, int]:
    tokens = list(tokenizer.encode(value))
    if len(tokens) <= limit:
        return value, len(tokens)
    for keep in range(min(limit, len(tokens)), -1, -1):
        candidate = tokenizer.decode(tokens[:keep]).strip()
        candidate_tokens = list(tokenizer.encode(candidate))
        if len(candidate_tokens) <= limit:
            return candidate, len(candidate_tokens)
    raise ValueError(f"unable to truncate similarity role within {limit} tokens")


def serialize_similarity_item(
    item: SimilarityItem,
    tokenizer: Tokenizer,
    config: SimilarityConfig,
) -> tuple[str, SimilarityTokenCounts]:
    """Serialize only the three approved roles, with model-token-aware limits."""
    title, title_count = _role_tokens(tokenizer, item.title, config.title_tokens)
    abstract, abstract_count = _role_tokens(tokenizer, item.abstract, config.abstract_tokens)
    summary, summary_count = _role_tokens(tokenizer, item.summary_zh, config.summary_tokens)

    def formatted() -> str:
        return f"T:{title}\nA:{abstract}\nS:{summary}"

    # Marker and boundary tokenization varies by tokenizer. Reduce role content
    # if the actual serialized sequence exceeds the hard model budget.
    role_values = [("title", title), ("abstract", abstract), ("summary", summary)]
    while len(tokenizer.encode(formatted())) > config.max_input_tokens:
        changed = False
        for index in (2, 1, 0):
            role, value = role_values[index]
            tokens = list(tokenizer.encode(value))
            empty_count = len(tokenizer.encode(""))
            if len(tokens) <= empty_count:
                continue
            truncated, _ = _role_tokens(tokenizer, value, len(tokens) - 1)
            role_values[index] = (role, truncated)
            changed = True
            break
        if not changed:
            raise ValueError(f"unable to serialize similarity input within {config.max_input_tokens} tokens: {item.id}")
        title, abstract, summary = (role_values[index][1] for index in range(3))
    counts = SimilarityTokenCounts(
        id=item.id,
        title=len(tokenizer.encode(title)),
        abstract=len(tokenizer.encode(abstract)),
        summary_zh=len(tokenizer.encode(summary)),
        total=len(tokenizer.encode(formatted())),
    )
    if counts.total > config.max_input_tokens:
        raise ValueError(f"similarity input exceeds token budget: {item.id}")
    return formatted(), counts


def _normalize(vector: Sequence[float], dimension: int, item_id: str) -> tuple[float, ...]:
    values = tuple(float(value) for value in vector)
    if len(values) != dimension or not all(math.isfinite(value) for value in values):
        raise ValueError(f"embedding dimension or value is invalid: {item_id}")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise ValueError(f"embedding vector is zero: {item_id}")
    return tuple(value / norm for value in values)


def _cosine_edges(
    items: Sequence[SimilarityItem],
    vectors: Sequence[Sequence[float]],
    config: SimilarityConfig,
) -> list[SimilarityEdge]:
    candidates: dict[str, list[tuple[float, str]]] = {item.id: [] for item in items}

    def keep_top_k(item_id: str, score: float, target_id: str) -> None:
        values = candidates[item_id]
        values.append((score, target_id))
        values.sort(key=lambda value: (-value[0], value[1]))
        del values[config.top_k:]

    for row_start in range(0, len(items), config.block_size):
        row_end = min(len(items), row_start + config.block_size)
        for col_start in range(row_start, len(items), config.block_size):
            col_end = min(len(items), col_start + config.block_size)
            for left in range(row_start, row_end):
                first_right = max(left + 1, col_start)
                for right in range(first_right, col_end):
                    score = sum(a * b for a, b in zip(vectors[left], vectors[right], strict=True))
                    if score >= config.min_cosine:
                        keep_top_k(items[left].id, score, items[right].id)
                        keep_top_k(items[right].id, score, items[left].id)

    ranked: dict[str, dict[str, int]] = {}
    scores: dict[tuple[str, str], float] = {}
    for item in items:
        values = candidates[item.id]
        ranked[item.id] = {target: rank for rank, (_, target) in enumerate(values, 1)}
        for score, target in values:
            scores[tuple(sorted((item.id, target)))] = score

    edges: list[SimilarityEdge] = []
    for source in sorted(ranked):
        for target, source_rank in ranked[source].items():
            target_rank = ranked.get(target, {}).get(source)
            if target_rank is None or source >= target:
                continue
            edges.append(SimilarityEdge(
                source_id=source,
                target_id=target,
                score=round(scores[(source, target)], config.score_decimals),
                source_rank=source_rank,
                target_rank=target_rank,
            ))
    return sorted(edges, key=lambda edge: (edge.source_id, edge.target_id))


def _artifact_parameters(config: SimilarityConfig) -> SimilarityParameters:
    return SimilarityParameters(
        max_input_tokens=config.max_input_tokens,
        title_tokens=config.title_tokens,
        abstract_tokens=config.abstract_tokens,
        summary_tokens=config.summary_tokens,
        separator_tokens=config.separator_tokens,
        top_k=config.top_k,
        min_cosine=config.min_cosine,
        mutual_top_k=config.mutual_top_k,
    )


def run_similarity(
    stage1: Path,
    paper: Path,
    blog: Path,
    repository_data: Path | None,
    output: Path,
    config: AppConfig,
    *,
    embedder: Embedder | None = None,
    tokenizer: Tokenizer | None = None,
) -> Path:
    """Compute and write a validated similarity artifact for one workflow run."""
    output = output.resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"similarity output already exists: {output}")
    started_at = time.perf_counter()
    manifests = [_manifest(stage1), _manifest(paper), _manifest(blog)]
    run_id = manifests[0]["run_id"]
    if any(value["run_id"] != run_id or value["schema_version"] != "1" for value in manifests):
        raise ValueError("similarity inputs must use the same run_id and schema_version")
    items = load_similarity_items(stage1, paper, blog, repository_data, config)
    texts: list[str] = []
    token_counts: list[SimilarityTokenCounts] = []
    truncated_items = 0
    cache_status = "not_used"
    vectors: list[tuple[float, ...]] = []
    if items:
        encoder = embedder or _FastEmbedder(config.settings.similarity)
        if tokenizer is None:
            tokenizer = getattr(encoder, "tokenizer", None)
        if tokenizer is None:
            raise ValueError("similarity tokenizer is required")
        for item in items:
            original_counts = (
                len(tokenizer.encode(item.title)),
                len(tokenizer.encode(item.abstract)),
                len(tokenizer.encode(item.summary_zh)),
            )
            text, counts = serialize_similarity_item(item, tokenizer, config.settings.similarity)
            texts.append(text)
            token_counts.append(counts)
            if original_counts != (counts.title, counts.abstract, counts.summary_zh):
                truncated_items += 1
        raw_vectors = list(encoder.embed(texts, batch_size=config.settings.similarity.batch_size))
        if len(raw_vectors) != len(items):
            raise ValueError("embedding count does not match similarity input count")
        vectors = [
            _normalize(vector, config.settings.similarity.dimension, item.id)
            for item, vector in zip(items, raw_vectors, strict=True)
        ]
        observed_cache_status = getattr(encoder, "cache_status", "not_observed")
        if observed_cache_status in {"hit", "miss", "not_observed"}:
            cache_status = observed_cache_status
        else:
            cache_status = "not_observed"
    similarity = SimilarityArtifact(
        run_id=run_id,
        schema_version="1",
        model=SimilarityModel(
            library=config.settings.similarity.library,
            version=config.settings.similarity.version,
            name=config.settings.similarity.model,
            dimension=config.settings.similarity.dimension,
            normalized=True,
        ),
        parameters=_artifact_parameters(config.settings.similarity),
        items_considered=len(items),
        encoded_items=len(vectors),
        token_counts=token_counts,
        edges=_cosine_edges(items, vectors, config.settings.similarity),
    )
    report = SimilarityReport(
        run_id=run_id,
        schema_version="1",
        model=similarity.model,
        items_considered=similarity.items_considered,
        encoded_items=similarity.encoded_items,
        truncated_items=truncated_items,
        edge_count=len(similarity.edges),
        top_k=config.settings.similarity.top_k,
        min_cosine=config.settings.similarity.min_cosine,
        elapsed_seconds=round(time.perf_counter() - started_at, 6),
        cache_status=cache_status,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        destination = temp_path / "similarity.json"
        write_json(destination, similarity.model_dump(mode="json"))
        write_json(temp_path / "similarity-report.json", report.model_dump(mode="json"))
        write_json(temp_path / "manifest.json", {"run_id": run_id, "schema_version": "1"})
        validate_similarity_artifact(
            destination,
            expected_item_ids={item.id for item in items},
            config=config.settings.similarity,
            run_id=run_id,
            schema_version="1",
        )
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"similarity output already exists: {output}")
        temp_path.chmod(0o755)
        temp_path.replace(output)
    except Exception:
        shutil.rmtree(temp_path, ignore_errors=True)
        raise
    return output / "similarity.json"


def _validate_artifact_files(path: Path, *, run_id: str, schema_version: str) -> None:
    if path.name != "similarity.json":
        raise ValueError("similarity artifact must be named similarity.json")
    directory = path.parent
    expected = {"manifest.json", "similarity-report.json", "similarity.json"}
    entries = list(directory.iterdir())
    actual = {entry.name for entry in entries}
    if actual != expected or any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ValueError("similarity artifact directory contains undeclared files")
    manifest = _manifest(directory)
    if manifest["run_id"] != run_id or manifest["schema_version"] != schema_version:
        raise ValueError("similarity artifact manifest does not match expected run")


def validate_similarity_artifact(
    path: Path,
    *,
    expected_item_ids: set[str],
    config: SimilarityConfig,
    run_id: str,
    schema_version: str,
) -> SimilarityArtifact:
    """Validate a similarity artifact at every downstream boundary."""
    _validate_artifact_files(path, run_id=run_id, schema_version=schema_version)
    artifact = SimilarityArtifact.model_validate(read_json(path))
    report = SimilarityReport.model_validate(read_json(path.parent / "similarity-report.json"))
    if artifact.run_id != run_id or artifact.schema_version != schema_version:
        raise ValueError("similarity artifact run_id or schema_version does not match inputs")
    if artifact.items_considered != len(expected_item_ids) or artifact.encoded_items != len(expected_item_ids):
        raise ValueError("similarity artifact does not cover the full canonical input")
    if artifact.model != SimilarityModel(
        library=config.library, version=config.version, name=config.model,
        dimension=config.dimension, normalized=True,
    ):
        raise ValueError("similarity artifact model does not match configuration")
    if artifact.parameters != _artifact_parameters(config):
        raise ValueError("similarity artifact parameters do not match configuration")
    if (
        report.run_id != run_id
        or report.schema_version != schema_version
        or report.model != artifact.model
        or report.items_considered != artifact.items_considered
        or report.encoded_items != artifact.encoded_items
        or report.edge_count != len(artifact.edges)
        or report.top_k != config.top_k
        or report.min_cosine != config.min_cosine
        or report.truncated_items > report.items_considered
    ):
        raise ValueError("similarity report does not match the validated artifact")
    token_ids = [value.id for value in artifact.token_counts]
    if token_ids != sorted(expected_item_ids):
        raise ValueError("similarity artifact token counts do not cover the full canonical input")
    for value in artifact.token_counts:
        if value.title > config.title_tokens or value.abstract > config.abstract_tokens or value.summary_zh > config.summary_tokens or value.total > config.max_input_tokens:
            raise ValueError(f"similarity token budget exceeded: {value.id}")
    edge_keys = [(edge.source_id, edge.target_id) for edge in artifact.edges]
    if edge_keys != sorted(edge_keys):
        raise ValueError("similarity edges are not in stable endpoint order")
    seen: set[tuple[str, str]] = set()
    ranks: dict[str, set[int]] = {item_id: set() for item_id in expected_item_ids}
    degrees: dict[str, int] = {item_id: 0 for item_id in expected_item_ids}
    ranked_scores: dict[str, list[tuple[int, float]]] = {item_id: [] for item_id in expected_item_ids}
    for edge in artifact.edges:
        if edge.source_id == edge.target_id:
            raise ValueError("similarity edge must not be a self-loop")
        if edge.source_id > edge.target_id:
            raise ValueError("similarity edge endpoints must be in stable ID order")
        if edge.source_id not in expected_item_ids or edge.target_id not in expected_item_ids:
            raise ValueError("similarity edge endpoint is invalid")
        key = (edge.source_id, edge.target_id)
        if key in seen:
            raise ValueError("duplicate similarity edge")
        seen.add(key)
        if not math.isfinite(edge.score) or edge.score < config.min_cosine:
            raise ValueError("similarity edge score violates the configured threshold")
        if edge.source_rank > config.top_k or edge.target_rank > config.top_k:
            raise ValueError("similarity edge violates threshold or Top-K")
        if round(edge.score, config.score_decimals) != edge.score:
            raise ValueError("similarity edge score is not stably rounded")
        for item_id, rank in ((edge.source_id, edge.source_rank), (edge.target_id, edge.target_rank)):
            degrees[item_id] += 1
            if degrees[item_id] > config.top_k:
                raise ValueError(f"similarity endpoint degree exceeds Top-K: {item_id}")
            if rank in ranks[item_id]:
                raise ValueError(f"similarity ranks must be unique for each endpoint: {item_id}")
            ranks[item_id].add(rank)
            ranked_scores[item_id].append((rank, edge.score))
    for item_id, values in ranked_scores.items():
        ordered = sorted(values)
        if any(left_score < right_score for (_, left_score), (_, right_score) in zip(ordered, ordered[1:])):
            raise ValueError(f"similarity rank/score order is invalid for endpoint: {item_id}")
    return artifact
