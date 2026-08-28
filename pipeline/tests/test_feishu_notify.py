from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self
from urllib.error import HTTPError

import pytest
from recsys_daily.feishu_notify import (
    FeishuNotificationError,
    generate_sign,
    load_feishu_config,
    run_notification,
    send_webhook,
    validate_feishu_webhook_url,
)

NOW = datetime(2026, 8, 26, 1, 9, tzinfo=UTC)
COMPLETED_AT = "2026-08-26T00:45:00Z"
ENVIRONMENT = {
    "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
    "FEISHU_WEBHOOK_SECRET": "test-secret",
    "SITE_ORIGIN": "https://example.github.io",
}
ROOT = Path(__file__).resolve().parents[2]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _write_config(root: Path, *, max_papers: int = 3, max_blogs: int = 3) -> None:
    _write_json(
        root / "config/feishu.json",
        {
            "template_id": "AAqP2jToTOo2R",
            "template_version": "1.0.0",
            "max_papers": max_papers,
            "max_blogs": max_blogs,
        },
    )


def _write_promoted_run(root: Path, *, papers: int, blogs: int, completed_at: str = COMPLETED_AT) -> None:
    _write_json(
        root / "data/state.json",
        {
            "schema_version": "1",
            "last_success_at": completed_at,
            "updated_at": completed_at,
            "sources": {},
            "recommended_item_ids": [],
        },
    )
    _write_json(
        root / "data/runs/2026/08/run-1.json",
        {
            "run_id": "run-1",
            "started_at": completed_at,
            "completed_at": completed_at,
            "paper_recommendations": papers,
            "blog_recommendations": blogs,
        },
    )


def _write_digest_and_items(root: Path, *, papers: int, blogs: int) -> None:
    paper_entries = []
    blog_entries = []
    for index in range(1, papers + 1):
        item_id = f"paper-{index}"
        paper_entries.append({"item_id": item_id, "rank": index, "recommendation_reason_zh": "推荐"})
        _write_json(
            root / f"data/items/papers/2026/08/{item_id}.json",
            {
                "id": item_id,
                "kind": "paper",
                "title": f"Paper [{index}] *Ranking*",
                "summary_zh": f"论文摘要 {index}",
            },
        )
    for index in range(1, blogs + 1):
        item_id = f"blog-{index}"
        blog_entries.append({"item_id": item_id, "rank": index, "recommendation_reason_zh": "推荐"})
        _write_json(
            root / f"data/items/blogs/2026/08/{item_id}.json",
            {
                "id": item_id,
                "kind": "blog",
                "title": f"Blog {index}",
                "summary_zh": f"博客摘要 {index}",
            },
        )
    _write_json(
        root / "data/digests/2026/08/2026-08-26.json",
        {"date": "2026-08-26", "papers": paper_entries, "blogs": blog_entries},
    )


class FakeResponse:
    def __init__(self, document: object):
        self.body = json.dumps(document).encode("utf-8")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class FakeOpener:
    def __init__(self, document: object = None):
        self.document = {"code": 0, "msg": "success"} if document is None else document
        self.requests = []
        self.timeouts = []

    def open(self, request: object, *, timeout: int) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        return FakeResponse(self.document)


def test_missing_secrets_skip_without_loading_config_or_sending(tmp_path: Path) -> None:
    opener = FakeOpener()
    result = run_notification(tmp_path, environ={}, now=NOW, opener=opener)
    assert result.status == "skipped"
    assert result.reason == "missing FEISHU_WEBHOOK_URL, FEISHU_WEBHOOK_SECRET"
    assert opener.requests == []


def test_repository_config_matches_the_published_template() -> None:
    config = load_feishu_config(ROOT)
    assert (config.template_id, config.template_version) == ("AAqP2jToTOo2R", "1.0.0")
    assert (config.max_papers, config.max_blogs) == (3, 3)


def test_stale_promoted_state_skips_without_sending(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _write_promoted_run(tmp_path, papers=0, blogs=0, completed_at="2026-08-25T00:45:00Z")
    opener = FakeOpener()
    result = run_notification(tmp_path, environ=ENVIRONMENT, now=NOW, opener=opener)
    assert result.status == "skipped"
    assert result.reason == "today's promoted state is not available"
    assert opener.requests == []


def test_successful_zero_digest_sends_empty_card_and_ignores_stale_digest(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _write_promoted_run(tmp_path, papers=0, blogs=0)
    _write_json(tmp_path / "data/digests/2026/08/2026-08-26.json", {"stale": True})
    opener = FakeOpener({"StatusCode": 0, "StatusMessage": "success"})

    result = run_notification(tmp_path, environ=ENVIRONMENT, now=NOW, opener=opener)

    assert result.status == "sent"
    assert result.reason == "papers=0, blogs=0"
    payload = json.loads(opener.requests[0].data.decode("utf-8"))
    variables = payload["card"]["data"]["template_variable"]
    assert variables["date"] == "2026-08-26"
    assert "今日暂无符合要求的论文。" in variables["content"]
    assert "今日暂无符合要求的技术博客。" in variables["content"]


def test_nonempty_digest_validates_items_and_limits_card_to_three_plus_three(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _write_promoted_run(tmp_path, papers=4, blogs=4)
    _write_digest_and_items(tmp_path, papers=4, blogs=4)
    opener = FakeOpener()

    result = run_notification(tmp_path, environ=ENVIRONMENT, now=NOW, opener=opener)

    assert result.status == "sent"
    request = opener.requests[0]
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["timestamp"] == str(int(NOW.timestamp()))
    assert payload["msg_type"] == "interactive"
    assert payload["card"]["type"] == "template"
    assert payload["card"]["data"]["template_id"] == "AAqP2jToTOo2R"
    assert payload["card"]["data"]["template_version_name"] == "1.0.0"
    content = payload["card"]["data"]["template_variable"]["content"]
    assert "Paper \\[1\\] \\*Ranking\\*" in content
    assert "paper-3" in content
    assert "paper-4" not in content
    assert "blog-3" in content
    assert "blog-4" not in content
    assert opener.timeouts == [15]


def test_digest_count_mismatch_skips_without_sending(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _write_promoted_run(tmp_path, papers=2, blogs=0)
    _write_digest_and_items(tmp_path, papers=1, blogs=0)
    opener = FakeOpener()

    result = run_notification(tmp_path, environ=ENVIRONMENT, now=NOW, opener=opener)

    assert result.status == "skipped"
    assert "count does not match" in result.reason
    assert opener.requests == []


def test_duplicate_matching_run_reports_skip_without_sending(tmp_path: Path) -> None:
    _write_config(tmp_path)
    _write_promoted_run(tmp_path, papers=0, blogs=0)
    report = json.loads((tmp_path / "data/runs/2026/08/run-1.json").read_text(encoding="utf-8"))
    report["run_id"] = "run-2"
    _write_json(tmp_path / "data/runs/2026/08/run-2.json", report)
    opener = FakeOpener()

    result = run_notification(tmp_path, environ=ENVIRONMENT, now=NOW, opener=opener)

    assert result.status == "skipped"
    assert "missing or ambiguous" in result.reason
    assert opener.requests == []


def test_signature_matches_feishu_hmac_contract() -> None:
    timestamp = 1_725_000_000
    secret = "fixed-secret"
    expected = base64.b64encode(
        hmac.new(f"{timestamp}\n{secret}".encode(), digestmod=hashlib.sha256).digest()
    ).decode()
    assert generate_sign(timestamp, secret) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://open.feishu.cn/open-apis/bot/v2/hook/token",
        "https://example.com/open-apis/bot/v2/hook/token",
        "https://open.feishu.cn/open-apis/bot/v2/hook/",
        "https://open.feishu.cn/open-apis/bot/v2/hook/token?copy=1",
    ],
)
def test_webhook_url_requires_official_https_endpoint(url: str) -> None:
    with pytest.raises(FeishuNotificationError, match="official Feishu"):
        validate_feishu_webhook_url(url)


def test_feishu_business_error_fails_delivery_without_exposing_response(tmp_path: Path) -> None:
    with pytest.raises(FeishuNotificationError, match="rejected the notification"):
        send_webhook(
            ENVIRONMENT["FEISHU_WEBHOOK_URL"],
            {"safe": "payload"},
            opener=FakeOpener({"code": 19021, "msg": "sign mismatch with sensitive detail"}),
        )


class HttpErrorOpener:
    def open(self, request: object, *, timeout: int) -> FakeResponse:
        raise HTTPError(request.full_url, 503, "service unavailable", {}, None)


def test_http_error_is_sanitized() -> None:
    with pytest.raises(FeishuNotificationError) as caught:
        send_webhook(
            ENVIRONMENT["FEISHU_WEBHOOK_URL"],
            {"safe": "payload"},
            opener=HttpErrorOpener(),
            max_attempts=1,
        )
    assert str(caught.value) == "Feishu webhook returned HTTP 503"
    assert "test-token" not in str(caught.value)


class RetryThenSuccessOpener:
    def __init__(self):
        self.attempts = 0

    def open(self, request: object, *, timeout: int) -> FakeResponse:
        self.attempts += 1
        if self.attempts == 1:
            raise HTTPError(request.full_url, 429, "rate limited", {"Retry-After": "2"}, None)
        return FakeResponse({"code": 0, "msg": "success"})


def test_retryable_http_error_respects_retry_after() -> None:
    opener = RetryThenSuccessOpener()
    delays = []
    send_webhook(
        ENVIRONMENT["FEISHU_WEBHOOK_URL"],
        {"safe": "payload"},
        opener=opener,
        sleeper=delays.append,
    )
    assert opener.attempts == 2
    assert delays == [2.0]
