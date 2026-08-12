from __future__ import annotations

from datetime import UTC, datetime

import pytest

from recsys_daily.rate_limit import RateLimiter, RetryableHTTPError, request_with_retries


def test_rate_limiter_enforces_four_second_interval_with_fake_clock() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def clock() -> float:
        return now[0]

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(target_rpm=30, hard_rpm=40, clock=clock, sleeper=sleep)
    limiter.acquire()
    now[0] = 1.0
    limiter.acquire()

    assert sleeps == [3.0]


def test_each_retry_acquires_limiter_and_retry_after_is_respected() -> None:
    calls = 0
    acquired = 0
    sleeps: list[float] = []

    def acquire() -> None:
        nonlocal acquired
        acquired += 1

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RetryableHTTPError(429, retry_after=2.5)
        return "ok"

    assert request_with_retries(operation, limiter=acquire, sleeper=sleeps.append, max_attempts=3) == "ok"
    assert calls == 3
    assert acquired == 3
    assert sleeps == [2.5, 2.5]


def test_unauthorized_error_is_not_retried() -> None:
    calls = 0

    def operation() -> None:
        nonlocal calls
        calls += 1
        raise RetryableHTTPError(401)

    with pytest.raises(RetryableHTTPError):
        request_with_retries(operation, limiter=lambda: None, sleeper=lambda _: None, max_attempts=3)
    assert calls == 1


def test_transport_errors_retry_with_exponential_backoff() -> None:
    calls = 0
    acquired = 0
    sleeps: list[float] = []

    def acquire() -> None:
        nonlocal acquired
        acquired += 1

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ConnectionError("tls eof")
        return "ok"

    assert request_with_retries(
        operation,
        limiter=acquire,
        sleeper=sleeps.append,
        max_attempts=3,
        retry_on_exceptions=(ConnectionError,),
        backoff_seconds=0.5,
    ) == "ok"
    assert calls == 3
    assert acquired == 3
    assert sleeps == [0.5, 1.0]


def test_numeric_retry_after_is_clamped_to_max_delay() -> None:
    sleeps: list[float] = []
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableHTTPError(503, retry_after=10_000)
        return "ok"

    assert request_with_retries(
        operation,
        sleeper=sleeps.append,
        max_attempts=2,
        max_delay_seconds=7,
    ) == "ok"
    assert sleeps == [7]


def test_http_date_retry_after_is_clamped_to_max_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    calls = 0
    monkeypatch.setattr("recsys_daily.rate_limit.time.time", lambda: 0)

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            retry_at = datetime(2099, 1, 1, tzinfo=UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
            raise RetryableHTTPError(503, retry_after=retry_at)  # type: ignore[arg-type]
        return "ok"

    assert request_with_retries(
        operation,
        sleeper=sleeps.append,
        max_attempts=2,
        max_delay_seconds=5,
    ) == "ok"
    assert sleeps == [5]


def test_exponential_backoff_is_clamped_to_max_delay() -> None:
    sleeps: list[float] = []
    calls = 0

    def operation() -> str:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise ConnectionError("offline")
        return "ok"

    assert request_with_retries(
        operation,
        sleeper=sleeps.append,
        max_attempts=4,
        retry_on_exceptions=(ConnectionError,),
        backoff_seconds=4,
        max_delay_seconds=6,
    ) == "ok"
    assert sleeps == [4, 6, 6]
