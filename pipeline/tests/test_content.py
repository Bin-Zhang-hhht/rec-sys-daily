from types import SimpleNamespace

import pytest

from recsys_daily import content


def test_fetch_bytes_rejects_declared_oversize_before_reading_body(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(headers={"Content-Length": "101"}, content=b"x")
    monkeypatch.setattr(content, "fetch_public_url", lambda *_args, **_kwargs: response)

    with pytest.raises(ValueError, match="content exceeds 100 bytes"):
        content.fetch_bytes("https://example.com/source", 100)


def test_domain_limiter_paces_same_hostname_without_blocking_other_hosts() -> None:
    now = [0.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    limiter = content.DomainRateLimiter(2, clock=lambda: now[0], sleeper=sleep)
    limiter.acquire("https://example.com/one")
    limiter.acquire("https://other.example.com/two")
    limiter.acquire("https://EXAMPLE.com/three")

    assert sleeps == [2.0]


def test_content_fetches_forward_retry_and_user_agent_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    response = SimpleNamespace(headers={}, content=b"article")

    def fetch(_url: str, **kwargs: object) -> object:
        calls.append(kwargs)
        return response

    monkeypatch.setattr(content, "fetch_public_url", fetch)

    assert content.fetch_bytes(
        "https://example.com/source",
        100,
        max_attempts=4,
        user_agent="RecSysDaily/test",
        attempt_limiter=lambda: None,
        backoff_seconds=0.25,
        max_delay_seconds=8,
    ) == b"article"
    assert content.fetch_text(
        "https://example.com/source",
        100,
        max_attempts=4,
        user_agent="RecSysDaily/test",
        attempt_limiter=lambda: None,
        backoff_seconds=0.25,
        max_delay_seconds=8,
    ) == "article"
    candidate = SimpleNamespace(url="https://example.com/article")
    assert content.fetch_article_html(
        candidate,
        100,
        max_attempts=4,
        user_agent="RecSysDaily/test",
        attempt_limiter=lambda: None,
        backoff_seconds=0.25,
        max_delay_seconds=8,
    ) == "article"
    assert len(calls) == 3
    for call in calls:
        assert call["timeout"] == 45
        assert call["max_attempts"] == 4
        assert call["user_agent"] == "RecSysDaily/test"
        assert callable(call["attempt_limiter"])
        assert call["backoff_seconds"] == 0.25
        assert call["max_delay_seconds"] == 8
