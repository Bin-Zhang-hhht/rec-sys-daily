from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import requests

from recsys_daily.config import MinerUConfig
from recsys_daily.mineru import MinerUClient, MinerUError


@dataclass
class FakeResponse:
    method: str
    status_code: int
    payload: dict[str, object]
    content: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    chunks: list[bytes] | None = None
    closed: bool = False
    chunk_sizes: list[int] = field(default_factory=list)

    def json(self) -> dict[str, object]:
        return self.payload

    def iter_content(self, *, chunk_size: int):
        self.chunk_sizes.append(chunk_size)
        yield from self.chunks if self.chunks is not None else (self.content,)

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def _request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        response = self.responses.pop(0)
        self.calls.append((method, url, kwargs))
        if isinstance(response, Exception):
            raise response
        assert response.method == method
        return response

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("post", url, **kwargs)

    def put(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("put", url, **kwargs)

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        return self._request("get", url, **kwargs)


def _zip_bytes(entries: dict[str, str]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return output.getvalue()


def _mineru_config(**overrides: Any) -> MinerUConfig:
    values: dict[str, object] = {
        "api_key_env": "MINERU_API_KEY",
        "base_url": "https://mineru.net/api/v4",
        "model_version": "vlm",
        "upload_timeout_seconds": 120,
        "poll_timeout_seconds": 900,
        "poll_interval_seconds": 5,
        "max_pdf_bytes": 20_971_520,
        "max_pdf_pages": 200,
    }
    values.update(overrides)
    return MinerUConfig.model_validate(values)


def _client(session: FakeSession, **kwargs: Any) -> MinerUClient:
    return MinerUClient(
        api_key="secret",
        config=_mineru_config(**kwargs.pop("config", {})),
        session=session,
        resolver=lambda _host, _port: ["8.8.8.8"],
        sleeper=lambda _seconds: None,
        page_counter=lambda _pdf: 1,
        **kwargs,
    )


def _happy_responses(zip_bytes: bytes) -> list[FakeResponse]:
    return [
        FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
        FakeResponse("put", 200, {}, content=b"uploaded"),
        FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"file_name": "paper.pdf", "data_id": "paper.pdf", "state": "running"}]}}),
        FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"file_name": "paper.pdf", "data_id": "paper.pdf", "state": "done", "full_zip_url": "https://result.example/batch-1.zip"}]}}),
        FakeResponse("get", 200, {}, content=zip_bytes),
    ]


def test_parse_pdf_uploads_polls_and_reads_full_markdown(tmp_path: Path) -> None:
    responses = _happy_responses(_zip_bytes({"full.md": "# Method\nMinerU text"}))
    result_response = responses[-1]
    session = FakeSession(responses)

    text = _client(session).parse_pdf(b"%PDF", "paper.pdf", tmp_path)

    assert text == "# Method\nMinerU text"
    assert session.calls[0][2]["json"]["model_version"] == "vlm"  # type: ignore[index]
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer secret"  # type: ignore[index]
    assert session.calls[1][2]["allow_redirects"] is False
    assert "headers" not in session.calls[-1][2]
    assert session.calls[-1][2]["stream"] is True
    assert result_response.closed is True
    assert not list(tmp_path.iterdir())


def test_parse_pdf_stops_chunked_result_after_limit_without_content_length(tmp_path: Path) -> None:
    responses = _happy_responses(b"")
    result_response = FakeResponse("get", 200, {}, chunks=[b"123", b"456"])
    responses[-1] = result_response

    with pytest.raises(MinerUError, match="result ZIP exceeds configured size limit"):
        _client(FakeSession(responses), config={"max_pdf_bytes": 5}).parse_pdf(b"%PDF", "paper.pdf", tmp_path)

    assert result_response.chunk_sizes == [6]
    assert result_response.closed is True
    assert not list(tmp_path.iterdir())


def test_public_result_redirect_closes_each_streamed_response() -> None:
    redirect = FakeResponse("get", 302, {}, headers={"Location": "https://cdn.example/result.zip"})
    result = FakeResponse("get", 200, {}, content=b"zip")
    session = FakeSession([redirect, result])

    content = _client(session)._public_get(
        "https://result.example/batch-1.zip",
        timeout=10,
        max_bytes=3,
    )

    assert content == b"zip"
    assert [call[1] for call in session.calls] == [
        "https://result.example/batch-1.zip",
        "https://cdn.example/result.zip",
    ]
    assert all(call[2]["stream"] is True for call in session.calls)
    assert redirect.closed is True
    assert result.closed is True


def test_parse_pdf_retries_transient_upload_failure(tmp_path: Path) -> None:
    responses = [
        FakeResponse("post", 503, {}, headers={"Retry-After": "0"}),
        *_happy_responses(_zip_bytes({"full.md": "ok"})),
    ]
    session = FakeSession(responses)

    assert _client(session).parse_pdf(b"%PDF", "paper.pdf", tmp_path) == "ok"
    assert [call[0] for call in session.calls[:2]] == ["post", "post"]


@pytest.mark.parametrize(
    ("file_urls", "message"),
    [
        (["https://upload.example/one", "https://upload.example/two"], "incomplete"),
        ([{"url": "https://upload.example/paper", "data_id": "other.pdf"}], "data_id"),
    ],
)
def test_parse_pdf_requires_one_matching_upload_entry(tmp_path: Path, file_urls: list[object], message: str) -> None:
    session = FakeSession([
        FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": file_urls}}),
    ])

    with pytest.raises(MinerUError, match=message):
        _client(session).parse_pdf(b"%PDF", "paper.pdf", tmp_path)

    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (
            [
                FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
                FakeResponse("put", 200, {}),
                FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"data_id": "paper.pdf", "state": "failed"}]}}),
            ],
            "failed",
        ),
        (
            [
                FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
                FakeResponse("put", 200, {}),
                FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"data_id": "other.pdf", "state": "running"}]}}),
            ],
            "data_id",
        ),
        (
            [
                FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
                FakeResponse("put", 200, {}),
                FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "other-batch", "extract_result": [{"data_id": "paper.pdf", "state": "running"}]}}),
            ],
            "batch_id",
        ),
        (
            [
                FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
                FakeResponse("put", 200, {}),
                FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"data_id": "paper.pdf", "state": "running"}, {"data_id": "paper.pdf", "state": "done"}]}}),
            ],
            "exactly once",
        ),
    ],
)
def test_parse_pdf_rejects_terminal_or_unmatched_poll_result(tmp_path: Path, responses: list[FakeResponse], message: str) -> None:
    with pytest.raises(MinerUError, match=message):
        _client(FakeSession(responses)).parse_pdf(b"%PDF", "paper.pdf", tmp_path)
    assert not list(tmp_path.iterdir())


def test_parse_pdf_rejects_polling_deadline(tmp_path: Path) -> None:
    responses = [
        FakeResponse("post", 200, {"code": 0, "data": {"batch_id": "batch-1", "file_urls": ["https://upload.example/paper"]}}),
        FakeResponse("put", 200, {}),
        FakeResponse("get", 200, {"code": 0, "data": {"batch_id": "batch-1", "extract_result": [{"data_id": "paper.pdf", "state": "running"}]}}),
    ]
    clock_values = iter([0.0, 2.0])
    with pytest.raises(MinerUError, match="deadline"):
        MinerUClient(
            api_key="secret",
            config=_mineru_config(poll_timeout_seconds=1),
            session=FakeSession(responses),
            resolver=lambda _host, _port: ["8.8.8.8"],
            clock=lambda: next(clock_values),
            sleeper=lambda _seconds: None,
            page_counter=lambda _pdf: 1,
        ).parse_pdf(b"%PDF", "paper.pdf", tmp_path)
    assert not list(tmp_path.iterdir())


def test_parse_pdf_rejects_missing_full_markdown(tmp_path: Path) -> None:
    session = FakeSession(_happy_responses(_zip_bytes({"other.md": "not full"})))
    with pytest.raises(MinerUError, match="full.md"):
        _client(session).parse_pdf(b"%PDF", "paper.pdf", tmp_path)
    assert not list(tmp_path.iterdir())


def test_parse_pdf_rejects_duplicate_full_markdown(tmp_path: Path) -> None:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("full.md", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("full.md", "second")
    session = FakeSession(_happy_responses(output.getvalue()))

    with pytest.raises(MinerUError, match="exactly one full.md"):
        _client(session).parse_pdf(b"%PDF", "paper.pdf", tmp_path)

    assert not list(tmp_path.iterdir())


def test_parse_pdf_rejects_pdf_size_and_page_limits(tmp_path: Path) -> None:
    with pytest.raises(MinerUError, match="bytes"):
        _client(FakeSession([]), config={"max_pdf_bytes": 3}).parse_pdf(b"%PDF", "paper.pdf", tmp_path)

    with pytest.raises(MinerUError, match="pages"):
        MinerUClient(
            api_key="secret",
            config=_mineru_config(max_pdf_pages=1),
            session=FakeSession([]),
            resolver=lambda _host, _port: ["8.8.8.8"],
            page_counter=lambda _pdf: 2,
        ).parse_pdf(b"%PDF", "paper.pdf", tmp_path)
