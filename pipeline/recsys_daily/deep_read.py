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
from .content import ContentServices, PageText, arxiv_urls
from .schemas import BlogReading, PaperReading, VisualAnalysis


class DeepReadError(RuntimeError):
    pass


@dataclass
class DeepReadServices:
    content: ContentServices
    temporary_root: Path
    text_reader: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]]
    vision_reader: Callable[[list[Path]], Mapping[str, Any]]
    max_pdf_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 80
    max_html_bytes: int = 5 * 1024 * 1024
    vision_profile: str = "nvidia_omni"
    vision_model: str = "configured-vision-model"


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


def _body_from_html(content: ContentServices, html: str) -> str:
    extractor = getattr(content, "extract_html", None) or content.extract_article
    return str(extractor(html) or html).strip()


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


def _visual_analysis(services: DeepReadServices, page_paths: list[Path], pages: list[int]) -> VisualAnalysis:
    if not pages:
        return VisualAnalysis(status="not_required")
    try:
        result = dict(services.vision_reader(page_paths))
    except Exception:
        return VisualAnalysis(status="unavailable")
    result.pop("reasoning_content", None)
    result.pop("reasoning_trace", None)
    result.update({"status": "completed", "profile": services.vision_profile, "model": services.vision_model, "pages": pages})
    try:
        return VisualAnalysis.model_validate(result)
    except Exception:
        return VisualAnalysis(status="unavailable")


def _validated_payload(model: type[BaseModel], payload: Mapping[str, Any], *, analysis_basis: str, visual: VisualAnalysis | None = None) -> BaseModel:
    data = dict(payload)
    for key in ("source_full_text", "raw_text", "full_text", "prompt", "response", "reasoning_content"):
        data.pop(key, None)
    data["analysis_basis"] = analysis_basis
    if visual is not None:
        data["visual_analysis"] = visual.model_dump()
    return model.model_validate(data)


def deep_read_paper(candidate: Candidate, services: DeepReadServices) -> PaperReading:
    paths: list[Path] = []
    body = ""
    basis = "abstract_fallback"
    visual = VisualAnalysis(status="not_required")
    try:
        arxiv_id = _arxiv_identifier(candidate)
        if arxiv_id:
            html_url, pdf_url = arxiv_urls(arxiv_id)
            try:
                html_method = getattr(services.content, "fetch_arxiv_html", services.content.fetch_text)
                html = _fetch(html_method, html_url, candidate, services.max_html_bytes)
                _write_temp(services.temporary_root, f"{stable_id(candidate)}.html", html, paths)
                body = _body_from_html(services.content, html)
                if body:
                    basis = "arxiv_html"
            except Exception:
                body = ""
            if not body:
                try:
                    pdf_method = getattr(services.content, "fetch_pdf", services.content.fetch_bytes)
                    pdf = _fetch(pdf_method, pdf_url, candidate, services.max_pdf_bytes)
                    pdf_path = _write_temp(services.temporary_root, f"{stable_id(candidate)}.pdf", pdf, paths)
                    body, page_texts = services.content.extract_pdf(pdf_path, services.max_pdf_pages)
                    if body:
                        basis = "pdf_text"
                        _write_temp(services.temporary_root, f"{stable_id(candidate)}.txt", body, paths)
                except Exception:
                    body = ""
                    basis = "abstract_fallback"
                    visual = VisualAnalysis(status="not_required")
                else:
                    # Text extraction is useful even if visual page handling
                    # fails; only the visual branch should become unavailable.
                    try:
                        page_numbers = services.content.critical_pages(page_texts)
                        page_paths = services.content.render_pages(pdf_path, page_numbers, services.temporary_root)
                        paths.extend(page_paths)
                        visual = _visual_analysis(services, page_paths, page_numbers)
                    except Exception:
                        visual = VisualAnalysis(status="unavailable")
        if not body:
            body = candidate.excerpt or candidate.title
        payload = services.text_reader("paper", body, {"analysis_basis": basis, "visual_analysis": visual.model_dump()})
        return _validated_payload(PaperReading, payload, analysis_basis=basis, visual=visual)  # type: ignore[return-value]
    finally:
        _cleanup(paths)


def deep_read_blog(candidate: Candidate, services: DeepReadServices) -> BlogReading:
    paths: list[Path] = []
    body = ""
    basis = "excerpt_fallback"
    try:
        feed_body = services.content.feed_content(candidate)
        if feed_body:
            body = str(feed_body).strip()
            basis = "rss_full_content"
        if not body and services.content.fetch_article_html is not None:
            try:
                html = services.content.fetch_article_html(candidate)
                _write_temp(services.temporary_root, f"{stable_id(candidate)}.html", html, paths)
                body = services.content.extract_article(html).strip()
                if body:
                    basis = "article_html"
            except Exception:
                body = ""
        if not body:
            body = candidate.excerpt or candidate.title
        payload = services.text_reader("blog", body, {"analysis_basis": basis})
        return _validated_payload(BlogReading, payload, analysis_basis=basis)  # type: ignore[return-value]
    finally:
        _cleanup(paths)


def _candidate_from_dict(value: Mapping[str, Any]) -> Candidate:
    data = dict(value)
    # The stable ID is an artifact identity field, not a Candidate dataclass
    # constructor argument; it is recomputed from the normalized identity.
    data.pop("id", None)
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
    max_candidates: int = 16,
) -> Path:
    if kind not in {"paper", "blog"}:
        raise ValueError("kind must be paper or blog")
    if max_candidates < 0:
        raise ValueError("max_candidates must be non-negative")
    source = input_dir / f"{kind}-candidates.json"
    if not source.exists():
        source = input_dir / "candidates.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    values = document.get("candidates", document) if isinstance(document, dict) else document
    if not isinstance(values, list):
        raise ValueError("candidate artifact must contain a list")
    if services is None:
        raise DeepReadError("deep_read requires injected services")
    readings = []
    kind_values = [value for value in values if isinstance(value, dict) and value.get("kind") == kind]
    for value in kind_values[:max_candidates]:
        candidate = _candidate_from_dict(value)
        reading = deep_read_paper(candidate, services) if kind == "paper" else deep_read_blog(candidate, services)
        readings.append({"id": stable_id(candidate), "kind": kind, "deep_reading": reading.model_dump(mode="json")})
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{kind}-deep-readings.json"
    destination.write_text(json.dumps({"kind": kind, "items": readings}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return destination
