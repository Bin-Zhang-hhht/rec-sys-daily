"""Public URL checks and redirect-safe HTTP fetching."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import ipaddress
from urllib.parse import urljoin, urlsplit
import time

import requests

from .rate_limit import request_with_retries


class PublicUrlError(ValueError):
    """Raised when a URL or resolved address is not safe to fetch."""


Resolver = Callable[[str, int], Iterable[tuple[object, ...]]]


def is_public_ip(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _resolved_ip(info: object) -> str | None:
    if isinstance(info, str):
        return info
    try:
        value = info[4]  # type: ignore[index]
        return str(value[0])
    except (IndexError, KeyError, TypeError):
        return None


def validate_public_url(url: str, *, resolver: Resolver | None = None) -> str:
    """Validate scheme and every DNS result, including direct IP literals."""
    if not isinstance(url, str) or len(url) > 2_048:
        raise PublicUrlError("URL is missing or too long")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise PublicUrlError("URL is malformed") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise PublicUrlError("only http and https URLs are allowed")
    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise PublicUrlError("URL has a malformed hostname") from exc
    if parsed.username or parsed.password or not hostname:
        raise PublicUrlError("URL must contain a public hostname without credentials")
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError as exc:
        raise PublicUrlError("URL has an invalid port") from exc
    host = hostname.rstrip(".")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip(literal):
            raise PublicUrlError(f"non-public address: {host}")
        return url
    resolve = resolver or __import__("socket").getaddrinfo
    try:
        results = list(resolve(host, port))
    except OSError as exc:
        raise PublicUrlError(f"could not resolve host: {host}") from exc
    addresses = [_resolved_ip(info) for info in results]
    addresses = [address for address in addresses if address]
    if not addresses or any(not is_public_ip(address) for address in addresses):
        raise PublicUrlError(f"host does not resolve only to public addresses: {host}")
    return url


def _fetch_public_url_once(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 45,
    resolver: Resolver | None = None,
    request: Callable[..., requests.Response] = requests.get,
    max_redirects: int = 5,
) -> requests.Response:
    """Fetch one redirect chain while validating every requested URL."""
    current = validate_public_url(url, resolver=resolver)
    for _ in range(max_redirects + 1):
        response = request(current, headers=headers, timeout=timeout, allow_redirects=False)
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            if not location:
                raise PublicUrlError("redirect response is missing Location")
            current = validate_public_url(urljoin(current, location), resolver=resolver)
            continue
        if response.status_code == 304 or 200 <= response.status_code < 300:
            return response
        raise requests.HTTPError(f"HTTP {response.status_code} for {current}", response=response)
    raise PublicUrlError("too many redirects")


def fetch_public_url(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 45,
    resolver: Resolver | None = None,
    request: Callable[..., requests.Response] = requests.get,
    max_redirects: int = 5,
    max_attempts: int = 3,
    user_agent: str | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    attempt_limiter: Callable[[], None] | None = None,
    backoff_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
) -> requests.Response:
    """Fetch a public URL with bounded retries and redirect revalidation."""
    request_headers = dict(headers or {})
    if user_agent and not any(str(name).casefold() == "user-agent" for name in request_headers):
        request_headers["User-Agent"] = user_agent

    return request_with_retries(
        lambda: _fetch_public_url_once(
            url,
            headers=request_headers,
            timeout=timeout,
            resolver=resolver,
            request=request,
            max_redirects=max_redirects,
        ),
        sleeper=sleeper,
        limiter=attempt_limiter,
        max_attempts=max_attempts,
        retry_on_exceptions=(requests.ConnectionError, requests.Timeout),
        backoff_seconds=backoff_seconds,
        max_delay_seconds=max_delay_seconds,
    )
