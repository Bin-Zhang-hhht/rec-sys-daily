"""Bounded MinerU v4 PDF upload, polling, and Markdown extraction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from io import BytesIO
import shutil
import time
from typing import Any
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader
import requests

from .config import MinerUConfig
from .rate_limit import RetryableHTTPError, request_with_retries
from .security import Resolver, _close_response, read_bounded_response, validate_public_url


class MinerUError(RuntimeError):
    """Raised when a MinerU operation cannot produce bounded full Markdown."""


class MinerUClient:
    def __init__(
        self,
        *,
        api_key: str,
        config: MinerUConfig,
        max_attempts: int = 3,
        session: requests.Session | Any | None = None,
        resolver: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        page_counter: Callable[[bytes], int] | None = None,
        backoff_seconds: float = 1.0,
        max_delay_seconds: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError("MinerU API key is required")
        self.api_key = api_key
        self.config = config
        self.max_attempts = max_attempts
        self.session = session or requests.Session()
        self.resolver = resolver
        self.clock = clock
        self.sleeper = sleeper
        self.page_counter = page_counter or self._count_pages
        self.backoff_seconds = backoff_seconds
        self.max_delay_seconds = max_delay_seconds

    @property
    def max_pdf_bytes(self) -> int:
        return self.config.max_pdf_bytes

    @staticmethod
    def _count_pages(pdf: bytes) -> int:
        try:
            return len(PdfReader(BytesIO(pdf), strict=False).pages)
        except Exception as exc:
            raise MinerUError("invalid PDF") from exc

    def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: float,
        allow_redirect_response: bool = False,
        response_reader: Callable[[Any], Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        request = getattr(self.session, method.lower())

        def operation() -> Any:
            try:
                response = request(url, timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                raise exc
            status = int(getattr(response, "status_code", 0))
            if status == 429 or 500 <= status <= 599:
                if kwargs.get("stream"):
                    _close_response(response)
                raise RetryableHTTPError(status, retry_after=getattr(response, "headers", {}).get("Retry-After"))
            if allow_redirect_response and 300 <= status < 400:
                return response
            if not 200 <= status < 300:
                if kwargs.get("stream"):
                    _close_response(response)
                raise MinerUError(f"MinerU HTTP {status}")
            return response_reader(response) if response_reader is not None else response

        try:
            return request_with_retries(
                operation,
                max_attempts=self.max_attempts,
                sleeper=self.sleeper,
                retry_on_exceptions=(requests.RequestException,),
                backoff_seconds=self.backoff_seconds,
                max_delay_seconds=self.max_delay_seconds,
            )
        except MinerUError:
            raise
        except Exception as exc:
            raise MinerUError(f"MinerU {method.upper()} failed") from exc

    def _public_get(self, url: str, *, timeout: float, max_bytes: int) -> bytes:
        def read_result(response: Any) -> bytes:
            try:
                return read_bounded_response(response, max_bytes)
            except ValueError as exc:
                raise MinerUError("MinerU result ZIP exceeds configured size limit") from exc

        current = validate_public_url(url, resolver=self.resolver)
        for _ in range(6):
            result = self._request(
                "get",
                current,
                timeout=timeout,
                allow_redirect_response=True,
                allow_redirects=False,
                stream=True,
                response_reader=read_result,
            )
            if isinstance(result, bytes):
                return result
            status = int(getattr(result, "status_code", 0))
            if 300 <= status < 400 or getattr(result, "is_redirect", False) or getattr(result, "is_permanent_redirect", False):
                location = getattr(result, "headers", {}).get("Location")
                if not location:
                    _close_response(result)
                    raise MinerUError("redirect response is missing Location")
                from urllib.parse import urljoin

                _close_response(result)
                current = validate_public_url(urljoin(current, location), resolver=self.resolver)
                continue
        raise MinerUError("too many redirects")

    @staticmethod
    def _json(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise MinerUError("MinerU returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("code", 0) not in (0, None):
            raise MinerUError("MinerU returned an error payload")
        return payload

    def parse_pdf(self, pdf: bytes, filename: str, temporary_root: Path) -> str:
        """Upload one PDF, poll one task, and return bounded full.md text."""
        if len(pdf) > self.config.max_pdf_bytes:
            raise MinerUError(f"PDF exceeds {self.config.max_pdf_bytes} bytes")
        if self.page_counter(pdf) > self.config.max_pdf_pages:
            raise MinerUError(f"PDF exceeds {self.config.max_pdf_pages} pages")
        if not filename or Path(filename).name != filename:
            raise MinerUError("invalid PDF filename")

        work_dir = temporary_root / f".mineru-{uuid4().hex}"
        work_dir.mkdir(parents=True, exist_ok=False)
        pdf_path = work_dir / filename
        zip_path = work_dir / "result.zip"
        try:
            pdf_path.write_bytes(pdf)
            headers = {"Authorization": f"Bearer {self.api_key}"}
            batch = self._json(
                self._request(
                    "post",
                    f"{self.config.base_url}/file-urls/batch",
                    timeout=self.config.upload_timeout_seconds,
                    headers=headers,
                    json={
                        "files": [{
                            "name": filename,
                            "data_id": filename,
                            "is_ocr": True,
                            "enable_formula": True,
                            "enable_table": True,
                            "language": "en",
                        }],
                        "model_version": self.config.model_version,
                    },
                )
            )
            data = batch.get("data")
            if not isinstance(data, dict):
                raise MinerUError("MinerU upload response has no data")
            batch_id = data.get("batch_id")
            file_urls = data.get("file_urls")
            if not isinstance(batch_id, str) or not isinstance(file_urls, list) or len(file_urls) != 1:
                raise MinerUError("MinerU upload response is incomplete")
            upload_entry = file_urls[0]
            if isinstance(upload_entry, dict) and upload_entry.get("data_id") not in (None, filename):
                raise MinerUError("MinerU upload data_id does not match request")
            upload_url = upload_entry.get("url") if isinstance(upload_entry, dict) else upload_entry
            if not isinstance(upload_url, str):
                raise MinerUError("MinerU upload URL is missing")
            validate_public_url(upload_url, resolver=self.resolver)
            self._request(
                "put",
                upload_url,
                timeout=self.config.upload_timeout_seconds,
                data=pdf,
                allow_redirects=False,
            )

            deadline = self.clock() + self.config.poll_timeout_seconds
            result_entry: dict[str, Any] | None = None
            while True:
                if self.clock() >= deadline:
                    raise MinerUError("MinerU polling deadline exceeded")
                poll = self._json(
                    self._request(
                        "get",
                        f"{self.config.base_url}/extract-results/batch/{batch_id}",
                        timeout=self.config.upload_timeout_seconds,
                        headers=headers,
                    )
                )
                poll_data = poll.get("data")
                if not isinstance(poll_data, dict) or poll_data.get("batch_id") != batch_id:
                    raise MinerUError("MinerU result batch_id does not match request")
                results = poll_data.get("extract_result") if isinstance(poll_data, dict) else None
                if not isinstance(results, list):
                    raise MinerUError("MinerU poll response has no extract_result")
                matches = [entry for entry in results if isinstance(entry, dict) and entry.get("data_id") == filename]
                if len(matches) != 1:
                    raise MinerUError("MinerU result data_id must match request exactly once")
                result_entry = matches[0]
                state = result_entry.get("state")
                if state == "done":
                    break
                if state == "failed":
                    raise MinerUError("MinerU extraction failed")
                if state not in {"waiting-file", "pending", "running", "converting"}:
                    raise MinerUError(f"MinerU returned unknown state: {state}")
                self.sleeper(self.config.poll_interval_seconds)

            result_url = result_entry.get("full_zip_url") if result_entry else None
            if not isinstance(result_url, str):
                raise MinerUError("MinerU result is missing full_zip_url")
            zip_content = self._public_get(
                result_url,
                timeout=self.config.upload_timeout_seconds,
                max_bytes=self.config.max_pdf_bytes,
            )
            zip_path.write_bytes(zip_content)
            try:
                with ZipFile(zip_path) as archive:
                    entries = [entry for entry in archive.infolist() if entry.filename == "full.md"]
                    if len(entries) != 1:
                        raise MinerUError("MinerU result must contain exactly one full.md")
                    entry = entries[0]
                    if entry.file_size > self.config.max_pdf_bytes:
                        raise MinerUError("MinerU full.md exceeds configured size limit")
                    content = archive.read(entry)
            except (BadZipFile, OSError) as exc:
                raise MinerUError("MinerU result ZIP is invalid") from exc
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise MinerUError("MinerU full.md is not UTF-8") from exc
            if not text.strip():
                raise MinerUError("MinerU full.md is empty")
            return text
        except MinerUError:
            raise
        except Exception as exc:
            raise MinerUError("MinerU PDF parsing failed") from exc
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
