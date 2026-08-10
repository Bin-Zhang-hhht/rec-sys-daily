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
