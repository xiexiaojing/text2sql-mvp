from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .column_labels import EntityColumnLabelIndex, resolve_column_display_labels
from .display_columns import filter_public_table
from .models import ExecutionResult
from .schema import SchemaCatalog

_COUNT_ALIAS_RE = re.compile(r"\bas\s+count\b", re.IGNORECASE)

_INTENT_SCALAR_ANSWERS: dict[str, str] = {
    "payment_order_count": "支付订单共 {value} 笔。",
    "merchant_count": "商户共 {value} 家。",
}


class ResultFormatter:
    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        entity_labels: EntityColumnLabelIndex | None = None,
    ) -> None:
        self.catalog = catalog
        self.entity_labels = entity_labels

    def format(
        self,
        execution: ExecutionResult,
        sql: str,
        sql_tables: list[str] | None = None,
        *,
        question: str | None = None,
        intent_id: str | None = None,
        display_name: str | None = None,
        output_type: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        if execution.mode == "dry_run":
            return (
                "SQL 已生成并通过安全校验；当前未配置真实 MySQL，只返回执行计划。",
                {"columns": [], "rows": [], "row_count": 0, "mode": "dry_run"},
            )
        column_labels = resolve_column_display_labels(
            execution.columns,
            self.catalog,
            sql_tables,
            self.entity_labels,
        )
        omit_table = _should_omit_scalar_table(execution.rows, output_type)
        if omit_table:
            table: dict[str, Any] = {
                "columns": [],
                "column_labels": [],
                "rows": [],
                "row_count": 0,
                "mode": execution.mode,
            }
        else:
            table = filter_public_table(
                {
                    "columns": execution.columns,
                    "column_labels": column_labels,
                    "rows": [_format_row_display_values(row) for row in execution.rows],
                    "row_count": len(execution.rows),
                    "mode": execution.mode,
                }
            )
        answer = self._answer_from_rows(
            execution.rows,
            sql,
            question=question,
            intent_id=intent_id,
            display_name=display_name,
            output_type=output_type,
        )
        return answer, table

    def _answer_from_rows(
        self,
        rows: list[dict[str, Any]],
        sql: str,
        *,
        question: str | None = None,
        intent_id: str | None = None,
        display_name: str | None = None,
        output_type: str | None = None,
    ) -> str:
        if not rows:
            return "查询完成，未返回记录。"
        first = rows[0]
        if len(rows) == 1 and "total" in first:
            return _answer_scalar_count(
                first["total"],
                question=question,
                intent_id=intent_id,
                display_name=display_name,
                output_type=output_type,
            )
        if _COUNT_ALIAS_RE.search(sql) and len(rows) == 1:
            value = next(iter(first.values()))
            return _answer_scalar_count(
                value,
                question=question,
                intent_id=intent_id,
                display_name=display_name,
                output_type=output_type,
            )
        return f"查询完成，返回 {len(rows)} 行。"


def _is_timestamp_column(key: str) -> bool:
    lowered = key.lower()
    return lowered.endswith(("_time", "_at", "_when"))


def _looks_like_epoch(value: Any) -> bool:
    if isinstance(value, str):
        return False
    if isinstance(value, dt.datetime):
        return True
    try:
        number = int(value)
    except (TypeError, ValueError):
        return False
    if number == 0:
        return False
    abs_number = abs(number)
    if abs_number >= 100_000_000_000:
        return True
    return 1_000_000_000 <= abs_number < 10_000_000_000


def _format_row_display_values(row: dict[str, Any]) -> dict[str, Any]:
    formatted = dict(row)
    for key, value in row.items():
        if _is_timestamp_column(key) and _looks_like_epoch(value):
            formatted[key] = _format_timestamp(value)
    return formatted


def _format_timestamp(value: Any) -> str:
    if value is None:
        return "暂无"
    if isinstance(value, dt.datetime):
        return value.strftime("%Y/%m/%d %H:%M")
    try:
        ms = int(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        return text or "暂无"
    if ms == 0:
        return "暂无"
    if 0 < ms < 10_000_000_000:
        ms *= 1000
    try:
        moment = dt.datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError):
        return str(value)
    if moment.year < 1900 or moment.year > 2100:
        return str(value)
    return moment.strftime("%Y/%m/%d %H:%M")


def _format_scalar_value(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _is_scalar_count_row(row: dict[str, Any]) -> bool:
    if len(row) != 1:
        return False
    value = next(iter(row.values()))
    if value is None:
        return True
    text = str(value).strip()
    return text.isdigit() or (text.replace(".", "", 1).isdigit() and text.count(".") <= 1)


def _should_omit_scalar_table(rows: list[dict[str, Any]], output_type: str | None) -> bool:
    if output_type == "scalar_count" and len(rows) == 1:
        return True
    if len(rows) == 1 and _is_scalar_count_row(rows[0]):
        return True
    return False


def _answer_scalar_count(
    value: Any,
    *,
    question: str | None,
    intent_id: str | None,
    display_name: str | None,
    output_type: str | None,
) -> str:
    formatted_value = _format_scalar_value(value)
    if intent_id and intent_id in _INTENT_SCALAR_ANSWERS:
        return _INTENT_SCALAR_ANSWERS[intent_id].format(value=formatted_value)
    normalized_question = (question or "").strip()
    if "支付订单" in normalized_question or "订单" in normalized_question:
        return f"支付订单共 {formatted_value} 笔。"
    if "商户" in normalized_question:
        return f"商户共 {formatted_value} 家。"
    if display_name:
        return f"{display_name}为 {formatted_value}。"
    if output_type == "scalar_count":
        return f"共 {formatted_value}。"
    return f"查询结果为 {formatted_value}。"
