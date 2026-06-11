#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from text2sql_runtime.evals import run_eval_cases
from text2sql_runtime.service import Text2SqlService


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    service = Text2SqlService.from_project_root(root)
    result = run_eval_cases(service, root / "eval_cases" / "cases.yaml")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
