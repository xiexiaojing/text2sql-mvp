from __future__ import annotations

from text2sql_runtime.evals import run_eval_cases


def test_seed_evals_run_in_dry_mode(project_root, service):
    result = run_eval_cases(service, project_root / "eval_cases" / "cases.yaml")

    assert result["total"] >= 8
    assert result["passed"] >= 8
    assert result["accuracy"] >= 0.8
