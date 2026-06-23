from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .semantics import epoch_ms_for_age_at_least, month_start_epoch_ms, year_start_epoch_ms

AGE_CUTOFF_RE = re.compile(r"\{\{age_cutoff_ms:(\d+)\}\}")


def resolve_golden_sql(golden_sql: str, *, eval_date: date | None = None) -> str:
    sql = golden_sql.strip()
    if "{{month_start_ms}}" in sql:
        sql = sql.replace("{{month_start_ms}}", str(month_start_epoch_ms(eval_date)))
    if "{{year_start_ms}}" in sql:
        sql = sql.replace("{{year_start_ms}}", str(year_start_epoch_ms(eval_date)))
    for match in AGE_CUTOFF_RE.finditer(golden_sql):
        age = int(match.group(1))
        sql = sql.replace(match.group(0), str(epoch_ms_for_age_at_least(age, eval_date)))
    return sql


def normalize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def normalize_row(row: dict[str, Any], columns: list[str] | None = None) -> dict[str, Any]:
    keys = columns if columns else sorted(row.keys())
    return {key: normalize_value(row.get(key)) for key in keys}


def normalize_rows(
    rows: list[dict[str, Any]],
    *,
    columns: list[str] | None = None,
    ignore_row_order: bool = True,
) -> list[dict[str, Any]]:
    normalized = [normalize_row(row, columns) for row in rows]
    if not ignore_row_order:
        return normalized
    return sorted(normalized, key=_row_sort_key)


def _row_sort_key(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)


def result_hash(rows: list[dict[str, Any]], *, columns: list[str] | None = None) -> str:
    payload = normalize_rows(rows, columns=columns, ignore_row_order=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def compare_result_sets(
    generated_rows: list[dict[str, Any]],
    golden_rows: list[dict[str, Any]],
    expected: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    compare_columns = expected.get("compare_columns")
    columns = [str(item) for item in compare_columns] if compare_columns else None
    ignore_row_order = expected.get("ignore_row_order", True)

    row_count_range = expected.get("row_count_range")
    if row_count_range is not None:
        minimum, maximum = int(row_count_range[0]), int(row_count_range[1])
        count = len(generated_rows)
        if count < minimum or count > maximum:
            reasons.append(f"generated row count {count} outside [{minimum}, {maximum}]")
        return (not reasons, reasons)

    expected_hash = expected.get("result_hash")
    if expected_hash:
        actual_hash = result_hash(generated_rows, columns=columns)
        if actual_hash != str(expected_hash):
            reasons.append(f"result_hash mismatch: {actual_hash}")
        return (not reasons, reasons)

    expected_rows = expected.get("result_rows")
    if expected_rows is not None:
        normalized_generated = normalize_rows(
            generated_rows,
            columns=columns,
            ignore_row_order=ignore_row_order,
        )
        normalized_expected = normalize_rows(
            [dict(item) for item in expected_rows],
            columns=columns,
            ignore_row_order=ignore_row_order,
        )
        if normalized_generated != normalized_expected:
            reasons.append(
                f"result_rows mismatch: generated={normalized_generated!r} expected={normalized_expected!r}"
            )
        return (not reasons, reasons)

    normalized_generated = normalize_rows(
        generated_rows,
        columns=columns,
        ignore_row_order=ignore_row_order,
    )
    normalized_golden = normalize_rows(
        golden_rows,
        columns=columns,
        ignore_row_order=ignore_row_order,
    )
    if normalized_generated != normalized_golden:
        reasons.append(
            "result mismatch against golden_sql: "
            f"generated_rows={len(normalized_generated)} golden_rows={len(normalized_golden)}"
        )
        if len(normalized_generated) <= 3 and len(normalized_golden) <= 3:
            reasons.append(f"generated={normalized_generated!r}")
            reasons.append(f"golden={normalized_golden!r}")
    return (not reasons, reasons)
