"""Paper and blog deep-reading orchestration with strict temporary cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from .collect import Candidate, stable_id
from .content import ContentServices, arxiv_pdf_url
from .mineru import MinerUClient
from .schemas import BlogReading, PaperReading


class DeepReadError(RuntimeError):
    pass


@dataclass
class DeepReadServices:
    content: ContentServices
    temporary_root: Path
    text_reader: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]
    mineru: MinerUClient
    max_html_bytes: int = 5 * 1024 * 1024
    blog_feed_content: Callable[[Candidate], str | None] | None = None


def _write_temp(root: Path, name: str, payload: bytes | str, paths: list[Path]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(payload if isinstance(payload, bytes) else payload.encode("utf-8"))
    paths.append(path)
    return path


def _cleanup(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _arxiv_identifier(candidate: Candidate) -> str | None:
    if candidate.arxiv_id:
        return candidate.arxiv_id
    value = candidate.source_entry_id or candidate.url or ""
    match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#]+)", value, re.I)
    if not match:
        return None
    return re.sub(r"\.(?:pdf|html)$", "", match.group(1), flags=re.I)


def _fetch(method: Callable[..., Any], url: str, candidate: Candidate, limit: int) -> Any:
    """Accept either the URL/limit contract or a candidate-oriented fake."""
    try:
        return method(url, limit)
    except TypeError:
        return method(candidate)


def _validated_payload(model: type[BaseModel], payload: Mapping[str, Any], *, analysis_basis: str) -> BaseModel:
    if not isinstance(payload, Mapping):
        raise ValueError("deep-reading response must be a JSON object")
    data = dict(payload)
    for key in ("source_full_text", "raw_text", "full_text", "prompt", "response", "reasoning_content", "reasoning_trace"):
        data.pop(key, None)
    data["analysis_basis"] = analysis_basis
    reading = model.model_validate(data)
    return validate_reading_quality(reading)


def validate_reading_quality(reading: PaperReading | BlogReading) -> PaperReading | BlogReading:
    """Reject structurally valid but content-free model responses."""
    def has_text(value: str | None) -> bool:
        return bool(value and value.strip())

    def has_any_text(values: list[str]) -> bool:
        return any(has_text(value) for value in values)

    if isinstance(reading, PaperReading):
        if not has_text(reading.problem_zh):
            raise ValueError("paper deep-reading response requires a meaningful problem")
        has_method_or_contribution = bool(
            has_text(reading.method_zh)
            or has_any_text(reading.contributions_zh)
        )
        if not has_method_or_contribution:
            raise ValueError("paper deep-reading response requires a method or contribution")
        experiments = reading.experiments
        has_evidence = bool(
            any(has_text(reference.section) for reference in reading.evidence_refs)
            or has_any_text(reading.limitations_zh)
            or has_any_text(experiments.datasets)
            or has_any_text(experiments.baselines)
            or has_any_text(experiments.metrics)
            or has_any_text(experiments.findings_zh)
        )
        if not has_evidence:
            raise ValueError("paper deep-reading response requires experiment, evidence, or limitation")
        return reading
    if not has_text(reading.system_context_zh):
        raise ValueError("blog deep-reading response requires meaningful system_context_zh")
    has_blog_analysis = bool(
        has_text(reading.architecture_zh)
        or has_text(reading.implementation_zh)
        or has_any_text(reading.lessons_zh)
        or any(has_text(reference.heading) or has_text(reference.section) for reference in reading.evidence_refs)
    )
    if not has_blog_analysis:
        raise ValueError("blog deep-reading response requires architecture, implementation, lesson, or evidence")
    return reading


def deep_read_paper(candidate: Candidate, services: DeepReadServices) -> PaperReading:
    body = candidate.excerpt or candidate.title
    basis = "abstract_fallback"
    arxiv_id = _arxiv_identifier(candidate)
    if arxiv_id:
        pdf_url = arxiv_pdf_url(arxiv_id)
        try:
            pdf_method = getattr(services.content, "fetch_pdf", services.content.fetch_bytes)
            pdf = _fetch(pdf_method, pdf_url, candidate, services.mineru.max_pdf_bytes)
            body = services.mineru.parse_pdf(pdf, f"{stable_id(candidate)}.pdf", services.temporary_root)
            basis = "mineru_full_text"
        except Exception:
            body = candidate.excerpt or candidate.title
            basis = "abstract_fallback"
    payload = services.text_reader(
        "paper",
        body,
        {"id": stable_id(candidate), "analysis_basis": basis},
    )
    return _validated_payload(PaperReading, payload, analysis_basis=basis)  # type: ignore[return-value]


def deep_read_blog(candidate: Candidate, services: DeepReadServices) -> BlogReading:
    paths: list[Path] = []
    body = ""
    basis = "excerpt_fallback"
    try:
        feed_body = services.content.feed_content(candidate)
        if not feed_body and services.blog_feed_content is not None:
            feed_body = services.blog_feed_content(candidate)
        if feed_body:
            body = str(feed_body).strip()
            basis = "rss_full_content"
        if not body and services.content.fetch_article_html is not None:
            try:
                fetch_article = services.content.fetch_article_html
                try:
                    html = fetch_article(candidate, services.max_html_bytes)  # type: ignore[call-arg]
                except TypeError as first_error:
                    try:
                        html = fetch_article(candidate)
                    except TypeError:
                        raise first_error
                _write_temp(services.temporary_root, f"{stable_id(candidate)}.html", html, paths)
                body = services.content.extract_article(html).strip()
                if body:
                    basis = "article_html"
            except Exception:
                body = ""
        if not body:
            body = candidate.excerpt or candidate.title
        payload = services.text_reader("blog", body, {"id": stable_id(candidate), "analysis_basis": basis})
        return _validated_payload(BlogReading, payload, analysis_basis=basis)  # type: ignore[return-value]
    finally:
        _cleanup(paths)


def _candidate_from_dict(value: Mapping[str, Any]) -> Candidate:
    data = dict(value)
    # The stable ID is an artifact identity field, not a Candidate dataclass
    # constructor argument; it is recomputed from the normalized identity.
    data.pop("id", None)
    for key in ("summary_zh", "targets", "scenarios", "tasks", "methods", "relevance_score", "degraded"):
        data.pop(key, None)
    published = data.get("published_at")
    if isinstance(published, str):
        data["published_at"] = datetime.fromisoformat(published.replace("Z", "+00:00"))
    for key in ("authors", "categories", "source_scenarios"):
        if key in data and isinstance(data[key], list):
            data[key] = tuple(data[key])
    return Candidate(**data)


def deep_read(
    kind: str,
    input_dir: Path,
    output_dir: Path,
    *,
    services: DeepReadServices | None = None,
    max_candidates: int = 20,
) -> Path:
    if kind not in {"paper", "blog"}:
        raise ValueError("kind must be paper or blog")
    if max_candidates < 0:
        raise ValueError("max_candidates must be non-negative")
    source = input_dir / f"{kind}-candidates.json"
    if not source.exists():
        raise ValueError(f"missing {kind} candidate artifact in {input_dir}")
    document = json.loads(source.read_text(encoding="utf-8"))
    values = document.get("candidates", document) if isinstance(document, dict) else document
    if not isinstance(values, list):
        raise ValueError("candidate artifact must contain a list")
    if services is None:
        raise DeepReadError("deep_read requires injected services")
    readings: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    kind_values = [value for value in values if isinstance(value, dict) and value.get("kind") == kind]
    candidates = [_candidate_from_dict(value) for value in kind_values[:max_candidates]]
    candidate_ids = [stable_id(candidate) for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError(f"duplicate {kind} candidate id")
    for candidate, candidate_id in zip(candidates, candidate_ids, strict=True):
        try:
            reading = deep_read_paper(candidate, services) if kind == "paper" else deep_read_blog(candidate, services)
        except (OSError, RuntimeError, ValueError) as exc:
            code = "invalid_deep_reading" if isinstance(exc, ValueError) else "deep_read_failed"
            failures.append({"id": candidate_id, "code": code})
            continue
        readings.append({"id": candidate_id, "kind": kind, "deep_reading": reading.model_dump(mode="json")})
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{kind}-deep-readings.json"
    destination.write_text(
        json.dumps({"kind": kind, "items": readings, "failures": failures}, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return destination
