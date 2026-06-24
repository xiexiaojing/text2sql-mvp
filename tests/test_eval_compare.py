from __future__ import annotations

from datetime import date

from text2sql_runtime.eval_compare import (
    compare_result_sets,
    normalize_rows,
    resolve_golden_sql,
    result_hash,
)
from text2sql_runtime.semantics import (
    epoch_ms_for_age_at_least,
    month_start_epoch_ms,
    year_start_epoch_ms,
)


def test_resolve_golden_sql_placeholders():
    eval_date = date(2026, 6, 15)
    sql = resolve_golden_sql(
        "SELECT COUNT(1) AS total FROM payment_order po WHERE po.create_time >= {{month_start_ms}} "
        "AND po.create_time >= {{year_start_ms}} AND po.born_at <= {{age_cutoff_ms:60}}",
        eval_date=eval_date,
    )
    assert "{{age_cutoff_ms:60}}" not in sql
    assert "{{month_start_ms}}" not in sql
    assert "{{year_start_ms}}" not in sql
    assert str(month_start_epoch_ms(eval_date)) in sql
    assert str(year_start_epoch_ms(eval_date)) in sql
    assert str(epoch_ms_for_age_at_least(60, eval_date)) in sql


def test_compare_result_sets_allows_different_sql_shapes():
    generated = [{"total": 123}]
    golden = [{"total": 123}]
    ok, reasons = compare_result_sets(generated, golden, {})
    assert ok is True
    assert reasons == []


def test_compare_result_sets_detects_mismatch():
    generated = [{"total": 123}]
    golden = [{"total": 456}]
    ok, reasons = compare_result_sets(generated, golden, {})
    assert ok is False
    assert reasons


def test_compare_result_sets_ignore_row_order():
    generated = [
        {"channel_value": "B", "total": 2},
        {"channel_value": "A", "total": 1},
    ]
    golden = [
        {"channel_value": "A", "total": 1},
        {"channel_value": "B", "total": 2},
    ]
    ok, reasons = compare_result_sets(generated, golden, {"ignore_row_order": True})
    assert ok is True
    assert reasons == []


def test_compare_result_rows_expected():
    generated = [{"total": 5}]
    golden = [{"total": 999}]
    ok, reasons = compare_result_sets(
        generated,
        golden,
        {"result_rows": [{"total": 5}]},
    )
    assert ok is True
    assert reasons == []


def test_compare_row_count_range():
    generated = [{"total": 5}, {"total": 6}]
    golden = []
    ok, reasons = compare_result_sets(generated, golden, {"row_count_range": [1, 3]})
    assert ok is True
    assert reasons == []


def test_result_hash_is_stable():
    rows = [{"channel_value": "A", "total": 1}, {"channel_value": "B", "total": 2}]
    assert result_hash(rows) == result_hash(list(reversed(rows)))


def test_normalize_rows_casts_decimal_like_values():
    normalized = normalize_rows([{"total": 1.0}])
    assert normalized == [{"total": 1}]
