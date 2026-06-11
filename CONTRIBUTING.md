# Contributing

Thanks for your interest in improving **text2sql-mvp**.

## Development setup

```bash
git clone <your-fork-url>
cd text2sql-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## What to change for your domain

1. `configs/whitelist_tables.yaml` — allowed tables, columns, joins, tenant column
2. `configs/business_semantics.yaml` — intents, semantic queries, SQL templates
3. `configs/semantic_overrides.yaml` — keyword concepts and sensitive search terms
4. `eval_cases/cases.yaml` — regression questions for dry-run evals

Run evals:

```bash
PYTHONPATH=apps/api/src:packages/text2sql_runtime/src python scripts/run_evals.py
```

## Pull requests

- Keep changes focused; avoid unrelated refactors.
- Add or update tests when behavior changes.
- Ensure `pytest` passes before opening a PR.

## Code style

- Python 3.11+
- `ruff` rules in `pyproject.toml`
