"""Shared pacing and bounded retry primitives for model requests."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import TypeVar

T = TypeVar("T")


class RetryableHTTPError(RuntimeError):
    """HTTP failure carrying the response status and optional server delay."""

    def __init__(self, status_code: int, message: str | None = None, retry_after: float | None = None) -> None:
        self.status_code = int(status_code)
        self.retry_after = retry_after
        super().__init__(message or f"HTTP {self.status_code}")


class RateLimiter:
    """A process-local limiter; acquisition is serialized for worker concurrency 1."""

    def __init__(
        self,
        *,
        target_rpm: int = 30,
        hard_rpm: int = 40,
        min_interval_seconds: float = 4.0,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if target_rpm <= 0 or hard_rpm <= 0 or target_rpm > hard_rpm:
            raise ValueError("invalid RPM limits")
        if min_interval_seconds <= 0:
            raise ValueError("min_interval_seconds must be positive")
        self.target_rpm = target_rpm
        self.hard_rpm = hard_rpm
        self.min_interval_seconds = max(float(min_interval_seconds), 60.0 / hard_rpm)
        self._clock = clock
        self._sleeper = sleeper
        self._last_acquired: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if self._last_acquired is not None:
                wait = self.min_interval_seconds - (now - self._last_acquired)
                if wait > 0:
                    self._sleeper(wait)
                    now = self._clock()
            self._last_acquired = now


def _status_code(error: Exception) -> int | None:
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _retry_after(error: Exception) -> float | None:
    value = getattr(error, "retry_after", None)
    if value is None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("Retry-After")
    try:
        return max(0.0, float(value)) if value is not None else None
    except (TypeError, ValueError):
        if isinstance(value, str):
            try:
                return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                pass
        return None


def request_with_retries(
    operation: Callable[[], T],
    *,
    limiter: RateLimiter | Callable[[], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
    retry_on_exceptions: tuple[type[Exception], ...] = (),
    backoff_seconds: float = 1.0,
) -> T:
    """Run an operation, retrying selected failures and pacing every attempt."""

    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must not be negative")
    acquire = limiter.acquire if hasattr(limiter, "acquire") else limiter
    for attempt in range(max_attempts):
        if acquire is not None:
            acquire()
        try:
            return operation()
        except Exception as error:
            status = _status_code(error)
            retryable = (
                status == 429
                or (status is not None and 500 <= status <= 599)
                or isinstance(error, retry_on_exceptions)
            )
            if not retryable or attempt == max_attempts - 1:
                raise
            delay = _retry_after(error)
            sleeper(delay if delay is not None else backoff_seconds * (2**attempt))
    raise RuntimeError("unreachable")
