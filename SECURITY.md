# Security

## Secrets and local configuration

Never commit these files:

- `.env.local` — MySQL credentials, LLM API keys, tenant defaults
- `configs/llm.yaml` — if you create a non-example LLM config with secrets
- `data/audit.sqlite3` — may contain real user questions from local testing

Use `.env.example` as a template. Copy it to `.env.local` and fill in your own values locally.

## Open-source defaults

The repository ships with:

- `TEXT2SQL_EXECUTION_MODE=dry_run` — no database connection required
- Empty LLM / MySQL environment variables
- Generic payment-domain demo schema (not production data)

## Reporting issues

If you find credentials accidentally committed to this repository, open a GitHub issue or contact the maintainer so they can be rotated and removed from history.
