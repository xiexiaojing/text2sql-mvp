from __future__ import annotations

from text2sql_runtime.evals import run_eval_cases


def test_seed_evals_run_in_dry_mode(project_root, service):
    result = run_eval_cases(service, project_root / "eval_cases" / "cases.yaml")

    assert result["total"] >= 8
    assert result["passed"] >= 8
    assert result["accuracy"] >= 0.8


def test_seed_eval_cases_define_golden_sql(project_root):
    from text2sql_runtime.config import load_yaml

    raw = load_yaml(project_root / "eval_cases" / "cases.yaml")
    golden_count = 0
    for case in raw.get("cases", []):
        if case.get("expected", {}).get("rejected"):
            continue
        golden_sql = case.get("golden_sql") or case.get("expected", {}).get("golden_sql")
        assert golden_sql, f"missing golden_sql for {case.get('id')}"
        golden_count += 1
    assert golden_count == 7
