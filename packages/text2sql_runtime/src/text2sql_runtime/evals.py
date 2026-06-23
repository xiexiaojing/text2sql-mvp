from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

from .config import load_yaml
from .eval_compare import compare_result_sets, resolve_golden_sql, result_hash
from .models import QueryInput, QueryResult
from .service import Text2SqlService


def run_eval_cases(
    service: Text2SqlService,
    cases_path: Path,
    *,
    eval_date: date | None = None,
) -> dict[str, Any]:
    raw = load_yaml(cases_path)
    live = service.settings.live_execution
    case_items = list(raw.get("cases", []))
    enabled_cases = [case for case in case_items if not case.get("pending")]
    pending_cases = [case for case in case_items if case.get("pending")]
    results: list[dict[str, Any]] = []
    passed = 0
    for case in case_items:
        case_result = _run_single_case(service, case, live=live, eval_date=eval_date)
        if case.get("pending"):
            results.append(case_result)
            continue
        if case_result["ok"]:
            passed += 1
        results.append(case_result)

    enabled_total = len(enabled_cases)
    total = len(case_items)
    summary = {
        "mode": "live_result_compare" if live else "dry_run_structural",
        "total": total,
        "enabled_total": enabled_total,
        "pending": len(pending_cases),
        "passed": passed,
        "failed": enabled_total - passed,
        "accuracy": round(passed / enabled_total, 4) if enabled_total else 0,
        "results": results,
    }
    print(
        f"[eval] mode={summary['mode']} passed={passed}/{enabled_total} pending={len(pending_cases)}",
        file=sys.stderr,
        flush=True,
    )
    return summary


def _log_eval_pass(question: str, *, structural: bool, golden: bool, rejection: bool = False) -> None:
    if rejection:
        print(f"{question} 拒绝校验通过。", flush=True)
        return
    if structural and golden:
        print(f"{question} SQL 结构校验通过, 标准 SQL对比通过。", flush=True)
        return
    if structural:
        print(f"{question} SQL 结构校验通过。", flush=True)


def _log_eval_fail(question: str, reasons: list[str]) -> None:
    detail = "; ".join(reasons) if reasons else "unknown"
    print(f"{question} 评测失败: {detail}", flush=True)


def _run_single_case(
    service: Text2SqlService,
    case: dict[str, Any],
    *,
    live: bool,
    eval_date: date | None,
) -> dict[str, Any]:
    question = str(case["question"])
    if case.get("pending"):
        return {
            "id": case.get("id"),
            "question": question,
            "status": "pending",
            "ok": True,
            "checks": ["pending"],
            "reasons": [],
            "elapsed_ms": 0,
            "sql": None,
            "golden_sql": None,
            "generated_row_count": 0,
            "golden_row_count": 0,
            "generated_result_hash": None,
            "golden_result_hash": None,
            "rejection_reason": None,
        }

    expected = dict(case.get("expected", {}))
    golden_sql = case.get("golden_sql") or expected.get("golden_sql")
    compare_results = expected.get("compare_results", bool(golden_sql))

    query_input = QueryInput(
        question=question,
        domain_id=str(case["domainId"]),
        user_id=str(case.get("userId", "eval")),
        allow_return_sql=True,
    )
    result = service.query(query_input)
    reasons: list[str] = []
    checks: list[str] = []
    structural_ok = True
    golden_ok = True

    if expected.get("rejected"):
        ok, reasons = _check_rejection(result, expected)
        checks.append("rejection")
        if ok:
            _log_eval_pass(question, structural=False, golden=False, rejection=True)
        else:
            _log_eval_fail(question, reasons)
    elif live and golden_sql and compare_results:
        structural_ok, structural_reasons = _check_structural(
            result.status,
            result.generated_sql or "",
            expected,
        )
        golden_ok, golden_reasons = _check_live_result_compare(
            service,
            case,
            result,
            expected,
            golden_sql=str(golden_sql),
            eval_date=eval_date,
        )
        checks.extend(["structural", "live_result_compare"])
        reasons = structural_reasons + golden_reasons
        ok = structural_ok and golden_ok
        if ok:
            _log_eval_pass(question, structural=True, golden=True)
        else:
            _log_eval_fail(question, reasons)
    else:
        structural_ok, reasons = _check_structural(result.status, result.generated_sql or "", expected)
        checks.append("structural")
        if golden_sql and not live:
            checks.append("golden_sql_skipped_dry_run")
        ok = structural_ok
        if ok:
            _log_eval_pass(question, structural=True, golden=False)
        else:
            _log_eval_fail(question, reasons)

    generated_rows = _rows_from_query_result(result)
    golden_rows: list[dict[str, Any]] = []
    if live and golden_sql and compare_results and result.status in {"ok", "planned"}:
        golden_rows = _execute_golden_sql(service, case, str(golden_sql), eval_date=eval_date)

    return {
        "id": case.get("id"),
        "question": question,
        "status": result.status,
        "ok": ok,
        "checks": checks,
        "reasons": reasons,
        "elapsed_ms": result.elapsed_ms,
        "sql": result.generated_sql,
        "golden_sql": resolve_golden_sql(str(golden_sql), eval_date=eval_date) if golden_sql else None,
        "generated_row_count": len(generated_rows),
        "golden_row_count": len(golden_rows) if golden_rows is not None else 0,
        "generated_result_hash": result_hash(generated_rows) if generated_rows else None,
        "golden_result_hash": result_hash(golden_rows) if golden_rows else None,
        "rejection_reason": result.rejection_reason,
    }


def _check_rejection(result: QueryResult, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if result.status != "rejected":
        reasons.append("expected rejection")
    reason_contains = expected.get("reason_contains")
    if reason_contains:
        haystack = " ".join(
            part
            for part in [result.rejection_reason or "", result.generated_sql or ""]
            if part
        )
        if reason_contains not in haystack:
            reasons.append(f"missing rejection reason fragment: {reason_contains}")
    return (not reasons, reasons)


def _check_structural(status: str, sql: str, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    lowered = sql.lower()
    if status not in {"ok", "planned"}:
        reasons.append(f"unexpected status {status}")
    for table in expected.get("tables", []):
        if str(table).lower() not in lowered:
            reasons.append(f"missing table {table}")
    features = set(expected.get("features", []))
    if "count" in features and "count(" not in lowered:
        reasons.append("missing count")
    if "group_by" in features and "group by" not in lowered:
        reasons.append("missing group by")
    if "order_by" in features and "order by" not in lowered:
        reasons.append("missing order by")
    if "domain_filter" in features and "domain_id" not in lowered and "domainid" not in lowered:
        reasons.append("missing domain filter")
    if "age_filter" in features and "born_at" not in lowered and "bornat" not in lowered:
        reasons.append("missing age filter")
    return (not reasons, reasons)


def _check_live_result_compare(
    service: Text2SqlService,
    case: dict[str, Any],
    result: QueryResult,
    expected: dict[str, Any],
    *,
    golden_sql: str,
    eval_date: date | None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if result.status not in {"ok", "planned"}:
        reasons.append(f"unexpected status {result.status}")
        return (False, reasons)

    generated_rows = _rows_from_query_result(result)
    golden_rows = _execute_golden_sql(service, case, golden_sql, eval_date=eval_date)
    ok, compare_reasons = compare_result_sets(generated_rows, golden_rows, expected)
    reasons.extend(compare_reasons)
    return (ok, reasons)


def _rows_from_query_result(result: QueryResult) -> list[dict[str, Any]]:
    if result.execution_rows is not None:
        return [dict(row) for row in result.execution_rows]
    if not result.table:
        return []
    rows = result.table.get("rows")
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows]


def _execute_golden_sql(
    service: Text2SqlService,
    case: dict[str, Any],
    golden_sql: str,
    *,
    eval_date: date | None,
) -> list[dict[str, Any]]:
    limits = service.settings.performance.get("limits", {})
    max_rows = int(limits.get("max_detail_limit", 1000))
    params = {"domain_id": str(case["domainId"])}
    expected = dict(case.get("expected", {}))
    extra_params = case.get("golden_params") or expected.get("golden_params") or {}
    if isinstance(extra_params, dict):
        params.update({str(key): value for key, value in extra_params.items()})

    resolved_sql = resolve_golden_sql(golden_sql, eval_date=eval_date)
    execution = service.executor.execute(resolved_sql, params, max_rows)
    return [dict(row) for row in execution.rows]
