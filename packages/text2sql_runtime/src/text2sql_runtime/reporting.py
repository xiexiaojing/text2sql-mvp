from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_REPORT_TZ = "Asia/Shanghai"


def report_timezone(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or DEFAULT_REPORT_TZ)


def ms_to_local_text(value_ms: int, tz: ZoneInfo) -> str:
    dt = datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M")


def hour_window_ms(
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
    hours: int = 1,
) -> tuple[int, int, str]:
    current = now.astimezone(tz) if now else datetime.now(tz)
    end = current.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    label = f"{start.strftime('%Y-%m-%d %H:%M')} ~ {end.strftime('%H:%M')}"
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000), label


def day_window_ms(
    *,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> tuple[int, int, str]:
    current = now.astimezone(tz) if now else datetime.now(tz)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    label = start.strftime("%Y-%m-%d")
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000), label


def format_hourly_unsupported_report(
    payload: dict[str, Any],
    *,
    window_label: str,
    tz: ZoneInfo,
) -> str:
    lines = [
        "【Text2SQL 未收录问题 · 小时报】",
        f"统计时段：{window_label}",
        f"新增未收录问题：{payload.get('unique', 0)} 条",
        "",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("本小时暂无新增未收录问题。")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        question = str(item.get("question") or "").strip()
        count = int(item.get("count") or 0)
        user_id = item.get("userId") or "-"
        domain_id = item.get("domainId") or "-"
        first_seen = ms_to_local_text(int(item.get("firstSeenAt") or 0), tz)
        lines.append(f"{index}. {question}")
        lines.append(f"   次数 {count} · 首次 {first_seen} · 用户 {user_id} · 域 {domain_id}")
    return "\n".join(lines)


def format_daily_top_questions_report(
    payload: dict[str, Any],
    *,
    day_label: str,
    tz: ZoneInfo,
) -> str:
    lines = [
        "【Text2SQL 用户提问 · 今日 TOP10】",
        f"统计日期：{day_label}",
        f"今日总提问：{payload.get('total', 0)} 次 · 去重 {payload.get('unique', 0)} 条",
        "",
    ]
    items = payload.get("items") or []
    if not items:
        lines.append("今日暂无用户提问。")
        return "\n".join(lines)

    for index, item in enumerate(items, start=1):
        question = str(item.get("question") or "").strip()
        count = int(item.get("count") or 0)
        unsupported_count = int(item.get("unsupportedCount") or 0)
        latest_seen = ms_to_local_text(int(item.get("latestSeenAt") or 0), tz)
        suffix = ""
        if unsupported_count:
            suffix = f" · 未收录 {unsupported_count} 次"
        lines.append(f"{index}. {question}（{count} 次{suffix} · 最近 {latest_seen}）")
    return "\n".join(lines)
