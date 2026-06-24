from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from text2sql_runtime.audit import SQLiteAuditStore
from text2sql_runtime.feishu import send_text_message
from text2sql_runtime.rejection_reasons import UNCONFIGURED_SEMANTIC_REASON
from text2sql_runtime.reporting import (
    day_window_ms,
    format_daily_top_questions_report,
    format_hourly_unsupported_report,
    hour_window_ms,
    report_timezone,
)


def _record(store: SQLiteAuditStore, **overrides):
    payload = {
        "query_id": "q-1",
        "created_at": 1_700_000_000_000,
        "user_id": "user-a",
        "domain_id": "domain-a",
        "question": "未收录的支付问题",
        "status": "rejected",
        "hit_path": "rejected",
        "rejection_reason": UNCONFIGURED_SEMANTIC_REASON,
        "elapsed_ms": 0,
        "scanned_rows": 0,
        "explain": [],
        "result": {},
        "warnings": [],
        "interaction_logs": [],
    }
    payload.update(overrides)
    store.record(payload)


def test_new_unsupported_questions_only_in_window(tmp_path):
    store = SQLiteAuditStore(tmp_path / "audit.sqlite3")
    _record(
        store,
        query_id="old-1",
        created_at=1_000,
        question="旧问题",
    )
    _record(
        store,
        query_id="new-1",
        created_at=5_000,
        question="新问题A",
        user_id="user-1",
    )
    _record(
        store,
        query_id="new-2",
        created_at=6_000,
        question="新问题A",
        user_id="user-1",
    )
    _record(
        store,
        query_id="planned-1",
        created_at=5_500,
        question="已支持问题",
        status="planned",
        rejection_reason=None,
    )

    payload = store.new_unsupported_questions(since_ms=4_000, until_ms=7_000)

    assert payload["unique"] == 1
    assert payload["items"][0]["question"] == "新问题A"
    assert payload["items"][0]["count"] == 2
    assert payload["items"][0]["userId"] == "user-1"


def test_new_unsupported_questions_excludes_users(tmp_path):
    store = SQLiteAuditStore(tmp_path / "audit.sqlite3")
    _record(store, query_id="chat-1", created_at=5_000, user_id="chat-ui", question="调试问题")
    _record(store, query_id="real-1", created_at=5_100, user_id="user-1", question="真实问题")

    payload = store.new_unsupported_questions(
        since_ms=4_000,
        until_ms=7_000,
        exclude_user_ids=("chat-ui",),
    )

    assert payload["unique"] == 1
    assert payload["items"][0]["question"] == "真实问题"


def test_top_questions_orders_by_count(tmp_path):
    store = SQLiteAuditStore(tmp_path / "audit.sqlite3")
    _record(store, query_id="a-1", created_at=1_000, question="问题A", status="planned", rejection_reason=None)
    _record(store, query_id="a-2", created_at=1_100, question="问题A", status="planned", rejection_reason=None)
    _record(store, query_id="a-3", created_at=1_150, question="问题A", status="planned", rejection_reason=None)
    _record(store, query_id="b-1", created_at=1_200, question="问题B", status="planned", rejection_reason=None)
    _record(
        store,
        query_id="b-2",
        created_at=1_300,
        question="问题B",
        status="rejected",
        rejection_reason=UNCONFIGURED_SEMANTIC_REASON,
    )

    payload = store.top_questions(since_ms=900, until_ms=2_000, limit=10)

    assert payload["total"] == 5
    assert payload["unique"] == 2
    assert payload["items"][0]["question"] == "问题A"
    assert payload["items"][0]["count"] == 3
    assert payload["items"][1]["question"] == "问题B"
    assert payload["items"][1]["unsupportedCount"] == 1


def test_report_windows_use_timezone():
    tz = report_timezone("Asia/Shanghai")
    now = datetime(2026, 6, 16, 20, 30, tzinfo=tz)
    since_ms, until_ms, label = hour_window_ms(tz=tz, now=now, hours=1)
    day_since, day_until, day_label = day_window_ms(tz=tz, now=now)

    assert label == "2026-06-16 19:00 ~ 20:00"
    assert day_label == "2026-06-16"
    assert since_ms < until_ms < day_until
    assert day_since <= since_ms


def test_format_reports_include_headings():
    tz = ZoneInfo("Asia/Shanghai")
    hourly = format_hourly_unsupported_report(
        {
            "unique": 1,
            "items": [
                {
                    "question": "某未收录问题",
                    "count": 1,
                    "firstSeenAt": 1_700_000_000_000,
                    "userId": "u1",
                    "domainId": "d1",
                }
            ],
        },
        window_label="2026-06-16 19:00 ~ 20:00",
        tz=tz,
    )
    daily = format_daily_top_questions_report(
        {
            "total": 3,
            "unique": 2,
            "items": [
                {
                    "question": "今天支付订单有多少",
                    "count": 2,
                    "latestSeenAt": 1_700_000_000_000,
                    "unsupportedCount": 0,
                }
            ],
        },
        day_label="2026-06-16",
        tz=tz,
    )

    assert "未收录问题" in hourly
    assert "某未收录问题" in hourly
    assert "今日 TOP10" in daily
    assert "今天支付订单有多少" in daily


def test_send_text_message(monkeypatch):
    captured: dict = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("text2sql_runtime.feishu.httpx.Client", FakeClient)

    send_text_message("hello", webhook_url="https://example.com/hook", keyword="通知")

    assert captured["url"] == "https://example.com/hook"
    assert captured["json"]["content"]["text"].startswith("通知\nhello")
