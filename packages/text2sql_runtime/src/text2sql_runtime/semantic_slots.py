from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Callable

from .semantics import (
    epoch_ms_for_age_at_least,
    month_end_epoch_ms,
    month_start_epoch_ms,
    week_end_epoch_ms,
    week_start_epoch_ms,
    year_start_epoch_ms,
)

PHONE_RE = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
CARD_RE = re.compile(r"(?<![A-Za-z0-9])([0-9Xx]{15,18})(?![A-Za-z0-9])")
FIELD_REF_RE = re.compile(r"\b([a-z][a-z0-9_]*)\s*\.\s*([a-z][a-z0-9_]*)\b", re.IGNORECASE)
AGE_RE = re.compile(r"(\d{2,3})\s*岁")
TOP_LIMIT_RE = re.compile(r"TOP\s*(\d+)", re.IGNORECASE)
PREFIX_LIMIT_RE = re.compile(r"前\s*(\d+)")

MONTH_SCOPE_MARKERS = ("本月", "这个月", "当月")
WEEK_SCOPE_MARKERS = ("本周", "这周", "当周")

LIKE_SLOT_PAIRS: dict[str, str] = {
    "merchant_name": "merchant_name_like",
    "area_name": "area_like",
    "category": "category_like",
    "field_name": "field_like",
    "entity_name": "entity_name_like",
    "label": "label_like",
}


@dataclass(frozen=True)
class SlotExtractionContext:
    question: str
    intent_id: str
    requested_slots: frozenset[str]
    slots: dict[str, Any]


SlotExtractor = Callable[[SlotExtractionContext], dict[str, Any]]


def extract_slots(
    question: str,
    *,
    intent_id: str,
    required_slots: tuple[str, ...],
    optional_slots: tuple[str, ...],
    slot_defaults: dict[str, Any],
) -> dict[str, Any]:
    slots: dict[str, Any] = dict(slot_defaults)
    requested = frozenset(required_slots) | frozenset(optional_slots) | frozenset(slots)
    context = SlotExtractionContext(
        question=question,
        intent_id=intent_id,
        requested_slots=requested,
        slots=slots,
    )
    for extractor in _SLOT_EXTRACTORS:
        for key, value in extractor(context).items():
            if not _empty(value):
                slots[key] = value
    return slots


def derive_slots(
    *,
    required_slots: tuple[str, ...],
    optional_slots: tuple[str, ...],
    slots: dict[str, Any],
) -> None:
    requested = set(required_slots) | set(optional_slots) | set(slots)
    if "age" in requested and _empty(slots.get("age")):
        slots["age"] = 60
    if "age_cutoff_ms" in requested or "age" in requested:
        age = int(slots.get("age") or 60)
        slots["age_cutoff_ms"] = epoch_ms_for_age_at_least(age)
    if "month_start_ms" in requested:
        slots["month_start_ms"] = month_start_epoch_ms()
    if "year_start_ms" in requested:
        slots["year_start_ms"] = year_start_epoch_ms()
    for source, like_target in LIKE_SLOT_PAIRS.items():
        if not _empty(slots.get(like_target)):
            slots[like_target] = _normalize_like_value(str(slots[like_target]))
        elif not _empty(slots.get(source)):
            slots[like_target] = _like_value(str(slots[source]))


def computed_values(slots: dict[str, Any]) -> dict[str, int]:
    age = int(slots.get("age") or 60)
    today = dt.date.today()
    return {
        "age_cutoff_ms": int(slots.get("age_cutoff_ms") or epoch_ms_for_age_at_least(age)),
        "age_cutoff_18_ms": int(slots.get("age_cutoff_18_ms") or epoch_ms_for_age_at_least(18)),
        "age_cutoff_35_ms": int(slots.get("age_cutoff_35_ms") or epoch_ms_for_age_at_least(35)),
        "age_cutoff_60_ms": int(slots.get("age_cutoff_60_ms") or epoch_ms_for_age_at_least(60)),
        "month_start_ms": int(slots.get("month_start_ms") or month_start_epoch_ms()),
        "month_end_ms": int(slots.get("month_end_ms") or month_end_epoch_ms(today)),
        "week_start_ms": int(slots.get("week_start_ms") or week_start_epoch_ms(today)),
        "week_end_ms": int(slots.get("week_end_ms") or week_end_epoch_ms(today)),
        "year_start_ms": int(slots.get("year_start_ms") or year_start_epoch_ms(today)),
        "last_year_start_ms": int(
            slots.get("last_year_start_ms") or year_start_epoch_ms(_add_years(today, -1))
        ),
        "last_year_end_ms": int(slots.get("last_year_end_ms") or year_start_epoch_ms(today)),
        "half_year_start_ms": int(
            slots.get("half_year_start_ms") or _date_to_epoch_ms(today - dt.timedelta(days=183))
        ),
        "result_limit": int(slots.get("result_limit") or 10),
    }


def _extract_temporal(context: SlotExtractionContext) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    requested = context.requested_slots
    question = context.question
    if "age" in requested:
        match = AGE_RE.search(question)
        slots["age"] = int(match.group(1)) if match else 60
    if "age_cutoff_ms" in requested or "age" in requested:
        age = int(slots.get("age") or context.slots.get("age") or 60)
        slots["age_cutoff_ms"] = epoch_ms_for_age_at_least(age)
    if "month_start_ms" in requested:
        slots["month_start_ms"] = month_start_epoch_ms()
    if "month_end_ms" in requested:
        slots["month_end_ms"] = month_end_epoch_ms()
    if "week_start_ms" in requested:
        slots["week_start_ms"] = week_start_epoch_ms()
    if "week_end_ms" in requested:
        slots["week_end_ms"] = week_end_epoch_ms()
    if "year_start_ms" in requested:
        slots["year_start_ms"] = year_start_epoch_ms()
    if "current_year" in requested:
        slots["current_year"] = dt.date.today().year
    if "current_month" in requested:
        slots["current_month"] = dt.date.today().month
    if "apply_month_scope" in requested and any(marker in question for marker in MONTH_SCOPE_MARKERS):
        slots["apply_month_scope"] = True
    if "apply_week_scope" in requested and any(marker in question for marker in WEEK_SCOPE_MARKERS):
        slots["apply_week_scope"] = True
    return slots


def _extract_identifiers(context: SlotExtractionContext) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    if "phone" in context.requested_slots:
        match = PHONE_RE.search(context.question)
        if match:
            slots["phone"] = match.group(1)
    if "card_no" in context.requested_slots:
        match = CARD_RE.search(context.question)
        if match:
            slots["card_no"] = match.group(1)
    return slots


def _extract_result_limit(context: SlotExtractionContext) -> dict[str, Any]:
    if "result_limit" not in context.requested_slots:
        return {}
    default = int(context.slots.get("result_limit") or 10)
    match = TOP_LIMIT_RE.search(context.question)
    if match:
        return {"result_limit": max(1, int(match.group(1)))}
    match = PREFIX_LIMIT_RE.search(context.question)
    if match:
        return {"result_limit": max(1, int(match.group(1)))}
    if "TOP3" in context.question.upper():
        return {"result_limit": 3}
    return {"result_limit": default}


def _extract_physical_field(context: SlotExtractionContext) -> dict[str, Any]:
    if context.intent_id != "field_explanation":
        return {}
    match = FIELD_REF_RE.search(context.question)
    if not match:
        return {}
    table_name = match.group(1).lower()
    column_name = match.group(2).lower()
    return {
        "table_name": table_name,
        "column_name": column_name,
        "field_name": f"{table_name}.{column_name}",
    }


_SLOT_EXTRACTORS: tuple[SlotExtractor, ...] = (
    _extract_temporal,
    _extract_identifiers,
    _extract_result_limit,
    _extract_physical_field,
)


def _add_years(value: dt.date, years: int) -> dt.date:
    return dt.date(value.year + years, value.month, value.day)


def _date_to_epoch_ms(value: dt.date) -> int:
    return int(dt.datetime.combine(value, dt.time.min).timestamp() * 1000)


def _like_value(value: str) -> str:
    return f"%{value}%"


def _normalize_like_value(value: str) -> str:
    cleaned = value.strip().strip("%").strip()
    return _like_value(cleaned) if cleaned else value


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
