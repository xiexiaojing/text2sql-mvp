from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_yaml
from .models import QueryInput
from .service import Text2SqlService


def run_eval_cases(service: Text2SqlService, cases_path: Path) -> dict[str, Any]:
    raw = load_yaml(cases_path)
    results = []
    passed = 0
    for case in raw.get("cases", []):
        query_input = QueryInput(
            question=str(case["question"]),
            domain_id=str(case["domainId"]),
            user_id="eval",
            allow_return_sql=True,
        )
        result = service.query(query_input)
        expected = dict(case.get("expected", {}))
        ok, reasons = _check_expectation(result.status, result.generated_sql or "", expected)
        if ok:
            passed += 1
        results.append(
            {
                "id": case.get("id"),
                "status": result.status,
                "ok": ok,
                "reasons": reasons,
                "elapsed_ms": result.elapsed_ms,
                "sql": result.generated_sql,
                "rejection_reason": result.rejection_reason,
            }
        )
    total = len(results)
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0,
        "results": results,
    }


def _check_expectation(status: str, sql: str, expected: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    lowered = sql.lower()
    if expected.get("rejected"):
        if status != "rejected":
            reasons.append("expected rejection")
        reason_contains = expected.get("reason_contains")
        if reason_contains and reason_contains not in lowered:
            pass
        return (not reasons, reasons)
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
