"""Send the promoted daily digest through a signed Feishu custom-bot webhook."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .rate_limit import RetryableHTTPError, request_with_retries

try:
    BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    BUSINESS_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")
SITE_BASE_PATH = "/rec-sys-daily/"
MAX_RESPONSE_BYTES = 65_536
REQUEST_TIMEOUT_SECONDS = 15
FEISHU_WEBHOOK_HOST = "open.feishu.cn"
FEISHU_WEBHOOK_PATH_PREFIX = "/open-apis/bot/v2/hook/"


class FeishuNotificationError(RuntimeError):
    """A configuration or delivery failure that must fail the workflow."""


class SkipNotification(RuntimeError):
    """A safe, expected reason not to send today's notification."""


@dataclass(frozen=True)
class FeishuConfig:
    template_id: str
    template_version: str
    max_papers: int
    max_blogs: int


@dataclass(frozen=True)
class PreparedNotification:
    business_date: date
    paper_recommendations: int
    blog_recommendations: int
    content: str


@dataclass(frozen=True)
class NotificationResult:
    status: str
    reason: str
    business_date: date | None = None


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _load_json_object(path: Path, *, failure: type[RuntimeError], label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise failure(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise failure(f"invalid {label}")
    return value


def _nonempty_string(value: object, *, failure: type[RuntimeError], label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise failure(f"invalid {label}")
    return value.strip()


def _bounded_count(value: object, *, label: str) -> int:
    if type(value) is not int or not 1 <= value <= 3:
        raise FeishuNotificationError(f"{label} must be an integer between 1 and 3")
    return value


def load_feishu_config(root: Path) -> FeishuConfig:
    document = _load_json_object(
        root / "config" / "feishu.json",
        failure=FeishuNotificationError,
        label="config/feishu.json",
    )
    expected = {"template_id", "template_version", "max_papers", "max_blogs"}
    if set(document) != expected:
        raise FeishuNotificationError("config/feishu.json has unexpected or missing fields")
    return FeishuConfig(
        template_id=_nonempty_string(
            document["template_id"], failure=FeishuNotificationError, label="template_id"
        ),
        template_version=_nonempty_string(
            document["template_version"], failure=FeishuNotificationError, label="template_version"
        ),
        max_papers=_bounded_count(document["max_papers"], label="max_papers"),
        max_blogs=_bounded_count(document["max_blogs"], label="max_blogs"),
    )


def validate_site_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise FeishuNotificationError("SITE_ORIGIN is invalid") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise FeishuNotificationError("SITE_ORIGIN must be an HTTPS origin without a path")
    return f"https://{parsed.hostname.lower()}"


def validate_feishu_webhook_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise FeishuNotificationError("FEISHU_WEBHOOK_URL is invalid") from exc
    token = parsed.path.removeprefix(FEISHU_WEBHOOK_PATH_PREFIX)
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != FEISHU_WEBHOOK_HOST
        or parsed.username
        or parsed.password
        or port is not None
        or not parsed.path.startswith(FEISHU_WEBHOOK_PATH_PREFIX)
        or not token
        or "/" in token
        or parsed.query
        or parsed.fragment
    ):
        raise FeishuNotificationError("FEISHU_WEBHOOK_URL must be an official Feishu custom-bot URL")
    return value


def _parse_utc_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SkipNotification(f"invalid {label}")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SkipNotification(f"invalid {label}") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise SkipNotification(f"invalid {label}")
    return timestamp.astimezone(UTC)


def _nonnegative_count(value: object, *, label: str) -> int:
    if type(value) is not int or value < 0:
        raise SkipNotification(f"invalid {label}")
    return value


def _load_matching_run_report(data_root: Path, state_timestamp: datetime, business_date: date) -> dict[str, Any]:
    runs_root = data_root / "runs"
    if not runs_root.is_dir():
        raise SkipNotification("today's promoted run report is missing")
    matches: list[dict[str, Any]] = []
    for path in sorted(runs_root.rglob("*.json")):
        report = _load_json_object(path, failure=SkipNotification, label="run report")
        completed_value = report.get("completed_at")
        if completed_value is None:
            continue
        completed_at = _parse_utc_timestamp(completed_value, label="run report completed_at")
        if completed_at == state_timestamp:
            matches.append(report)
    if len(matches) != 1:
        raise SkipNotification("today's promoted run report is missing or ambiguous")
    report = matches[0]
    completed_at = _parse_utc_timestamp(report.get("completed_at"), label="run report completed_at")
    if completed_at.astimezone(BUSINESS_TIMEZONE).date() != business_date:
        raise SkipNotification("today's promoted run report does not match the business date")
    _nonempty_string(report.get("run_id"), failure=SkipNotification, label="run report run_id")
    _nonnegative_count(report.get("paper_recommendations"), label="paper_recommendations")
    _nonnegative_count(report.get("blog_recommendations"), label="blog_recommendations")
    return report


def _load_ranked_entries(document: dict[str, Any], group: str, expected_count: int) -> list[dict[str, Any]]:
    raw_entries = document.get(group)
    if not isinstance(raw_entries, list) or len(raw_entries) != expected_count:
        raise SkipNotification(f"today's digest {group} count does not match the run report")
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise SkipNotification(f"invalid digest {group} entry")
        item_id = _nonempty_string(raw.get("item_id"), failure=SkipNotification, label="digest item_id")
        rank = raw.get("rank")
        if type(rank) is not int or rank < 1:
            raise SkipNotification(f"invalid digest {group} rank")
        entries.append({"item_id": item_id, "rank": rank})
    entries.sort(key=lambda entry: (entry["rank"], entry["item_id"]))
    if [entry["rank"] for entry in entries] != list(range(1, expected_count + 1)):
        raise SkipNotification(f"invalid digest {group} ranking")
    if len({entry["item_id"] for entry in entries}) != len(entries):
        raise SkipNotification(f"duplicate digest {group} item")
    return entries


def _load_digest(
    data_root: Path,
    business_date: date,
    paper_count: int,
    blog_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = (
        data_root
        / "digests"
        / f"{business_date.year:04d}"
        / f"{business_date.month:02d}"
        / f"{business_date.isoformat()}.json"
    )
    document = _load_json_object(path, failure=SkipNotification, label="today's digest")
    if document.get("date") != business_date.isoformat():
        raise SkipNotification("today's digest has the wrong date")
    return (
        _load_ranked_entries(document, "papers", paper_count),
        _load_ranked_entries(document, "blogs", blog_count),
    )


def _load_referenced_items(
    data_root: Path,
    entries: Sequence[dict[str, Any]],
    *,
    kind: str,
) -> list[dict[str, str]]:
    needed = {entry["item_id"] for entry in entries}
    found: dict[str, dict[str, str]] = {}
    item_root = data_root / "items" / ("papers" if kind == "paper" else "blogs")
    if needed and not item_root.is_dir():
        raise SkipNotification(f"canonical {kind} items are missing")
    for path in sorted(item_root.rglob("*.json")) if item_root.is_dir() else []:
        document = _load_json_object(path, failure=SkipNotification, label=f"canonical {kind} item")
        item_id = document.get("id")
        if item_id not in needed:
            continue
        if item_id in found:
            raise SkipNotification(f"duplicate canonical item: {item_id}")
        if document.get("kind") != kind:
            raise SkipNotification(f"canonical item kind mismatch: {item_id}")
        found[item_id] = {
            "id": item_id,
            "title": _nonempty_string(
                document.get("title"), failure=SkipNotification, label=f"canonical item title: {item_id}"
            ),
            "summary_zh": _nonempty_string(
                document.get("summary_zh"),
                failure=SkipNotification,
                label=f"canonical item summary: {item_id}",
            ),
            "kind": kind,
        }
    missing = needed - set(found)
    if missing:
        raise SkipNotification(f"digest references missing canonical {kind} items")
    return [found[entry["item_id"]] for entry in entries]


def _markdown_text(value: str) -> str:
    compact = " ".join(value.split())
    for marker in ("\\", "`", "*", "_", "~", "[", "]", "<", ">"):
        compact = compact.replace(marker, f"\\{marker}")
    return compact


def _item_url(site_origin: str, item: Mapping[str, str]) -> str:
    section = "papers" if item["kind"] == "paper" else "articles"
    return f"{site_origin}{SITE_BASE_PATH}{section}/{quote(item['id'], safe='')}/"


def _format_section(
    title: str,
    items: Sequence[Mapping[str, str]],
    site_origin: str,
) -> str:
    lines = [f"**{title} · {len(items)} 篇**", ""]
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"**{index:02d} · [{_markdown_text(item['title'])}]({_item_url(site_origin, item)})**",
                _markdown_text(item["summary_zh"]),
            ]
        )
        if index != len(items):
            lines.append("")
    return "\n".join(lines)


def build_markdown_content(
    papers: Sequence[Mapping[str, str]],
    blogs: Sequence[Mapping[str, str]],
    site_origin: str,
) -> str:
    sections: list[str] = []
    if papers:
        sections.append(_format_section("📘 论文精选", papers, site_origin))
    if blogs:
        sections.append(_format_section("📝 技术博客", blogs, site_origin))
    if not sections:
        raise FeishuNotificationError("card content requires at least one recommendation")
    return "\n\n".join(sections)


def prepare_notification(
    root: Path,
    config: FeishuConfig,
    site_origin: str,
    *,
    now: datetime,
) -> PreparedNotification:
    if now.tzinfo is None:
        raise FeishuNotificationError("current time must be timezone-aware")
    business_date = now.astimezone(BUSINESS_TIMEZONE).date()
    data_root = root / "data"
    state = _load_json_object(data_root / "state.json", failure=SkipNotification, label="data/state.json")
    state_timestamp = _parse_utc_timestamp(state.get("last_success_at"), label="state last_success_at")
    if state_timestamp.astimezone(BUSINESS_TIMEZONE).date() != business_date:
        raise SkipNotification("today's promoted state is not available")
    report = _load_matching_run_report(data_root, state_timestamp, business_date)
    paper_count = _nonnegative_count(report.get("paper_recommendations"), label="paper_recommendations")
    blog_count = _nonnegative_count(report.get("blog_recommendations"), label="blog_recommendations")

    if paper_count == 0 and blog_count == 0:
        raise SkipNotification("today's promoted digest has no recommendations")

    validated_site_origin = validate_site_origin(site_origin)
    paper_entries, blog_entries = _load_digest(data_root, business_date, paper_count, blog_count)
    papers = _load_referenced_items(data_root, paper_entries, kind="paper")[: config.max_papers]
    blogs = _load_referenced_items(data_root, blog_entries, kind="blog")[: config.max_blogs]

    return PreparedNotification(
        business_date=business_date,
        paper_recommendations=paper_count,
        blog_recommendations=blog_count,
        content=build_markdown_content(papers, blogs, validated_site_origin),
    )


def generate_sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def build_webhook_payload(
    prepared: PreparedNotification,
    config: FeishuConfig,
    secret: str,
    *,
    timestamp: int,
) -> dict[str, Any]:
    return {
        "timestamp": str(timestamp),
        "sign": generate_sign(timestamp, secret),
        "msg_type": "interactive",
        "card": {
            "type": "template",
            "data": {
                "template_id": config.template_id,
                "template_version_name": config.template_version,
                "template_variable": {
                    "date": prepared.business_date.isoformat(),
                    "content": prepared.content,
                },
            },
        },
    }


def _response_code(document: dict[str, Any]) -> int:
    for key in ("code", "StatusCode"):
        value = document.get(key)
        if type(value) is int:
            return value
    raise FeishuNotificationError("Feishu webhook returned an invalid response")


def send_webhook(
    webhook_url: str,
    payload: Mapping[str, Any],
    *,
    opener: Any | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    max_attempts: int = 3,
    sleeper: Any = time.sleep,
) -> None:
    request = Request(
        validate_feishu_webhook_url(webhook_url),
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    active_opener = opener or build_opener(_NoRedirectHandler())

    def send_once() -> bytes:
        try:
            with active_opener.open(request, timeout=timeout) as response:
                return response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            status = exc.code
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            exc.close()
            if status == 429 or 500 <= status <= 599:
                raise RetryableHTTPError(status, retry_after=retry_after) from None
            raise FeishuNotificationError(f"Feishu webhook returned HTTP {status}") from None

    try:
        body = request_with_retries(
            send_once,
            sleeper=sleeper,
            max_attempts=max_attempts,
            retry_on_exceptions=(OSError, URLError),
        )
    except RetryableHTTPError as exc:
        raise FeishuNotificationError(f"Feishu webhook returned HTTP {exc.status_code}") from None
    except (OSError, URLError):
        raise FeishuNotificationError("Feishu webhook request failed") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise FeishuNotificationError("Feishu webhook response is too large")
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FeishuNotificationError("Feishu webhook returned invalid JSON") from exc
    if not isinstance(document, dict) or _response_code(document) != 0:
        raise FeishuNotificationError("Feishu webhook rejected the notification")


def run_notification(
    root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
    opener: Any | None = None,
) -> NotificationResult:
    values = os.environ if environ is None else environ
    webhook_url = values.get("FEISHU_WEBHOOK_URL", "").strip()
    secret = values.get("FEISHU_WEBHOOK_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("FEISHU_WEBHOOK_URL", webhook_url),
            ("FEISHU_WEBHOOK_SECRET", secret),
        )
        if not value
    ]
    if missing:
        return NotificationResult(status="skipped", reason=f"missing {', '.join(missing)}")

    current = now or datetime.now(UTC)
    config = load_feishu_config(root)
    try:
        prepared = prepare_notification(root, config, values.get("SITE_ORIGIN", ""), now=current)
    except SkipNotification as exc:
        return NotificationResult(status="skipped", reason=str(exc))

    timestamp = int(current.timestamp())
    payload = build_webhook_payload(prepared, config, secret, timestamp=timestamp)
    send_webhook(webhook_url, payload, opener=opener)
    return NotificationResult(
        status="sent",
        reason=(
            f"papers={prepared.paper_recommendations}, "
            f"blogs={prepared.blog_recommendations}"
        ),
        business_date=prepared.business_date,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="repository root")
    arguments = parser.parse_args(argv)
    try:
        result = run_notification(arguments.root.resolve())
    except FeishuNotificationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.status == "sent":
        print(f"sent: {result.business_date.isoformat()} ({result.reason})")
    else:
        print(f"skipped: {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
