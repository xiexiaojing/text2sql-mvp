# text2sql-mvp

Open-source **Text-to-SQL MVP** with guarded SQL generation, semantic intent routing, multi-turn conversation support, and optional chart responses.

This repository is a **generic reference implementation**. The bundled demo uses a small **payment domain** (`payment_order`, `refund_order`, `merchant`). Replace the YAML configs with your own schema and business semantics.

## Features

- **Whitelist-first schema** — only approved tables/columns/joins are reachable
- **Business semantics layer** — questions map to reviewed SQL templates before any LLM fallback
- **SQL guard** — SELECT-only, AST checks, automatic tenant filter injection, row limits
- **Conversation context** — follow-ups like「那按状态呢」or「折线图也生成一下」rewrite safely
- **Chart engine** — pie/bar/line/radar/rose/funnel and more via `echartsOption` + markdown fences
- **Fast semantic path** — example-matched questions skip LLM slot extraction (`TEXT2SQL_LLM_SLOT_POLICY`)
- **Public table hygiene** — hide `id` / `*_id` columns from API responses
- **Dry-run by default** — plan SQL and audit without a live database
- **HTTP API + MCP server** — same runtime behind REST and MCP tools
- **Built-in chat UI** — `/chat` for local demos

## Quick start

```bash
cd text2sql-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
./start.sh
```

Open:

- Health: http://127.0.0.1:8777/health
- Chat UI: http://127.0.0.1:8777/chat

Stop:

```bash
./stop.sh
```

## Demo questions

| Question | Intent |
|----------|--------|
| 支付订单总数是多少 | `payment_order_count` |
| 支付订单按渠道统计 | `payment_channel_stat` |
| 支付订单按状态统计 | `payment_status_stat` |
| 各支付渠道交易金额分布 | `payment_channel_amount_distribution` |
| 近7天每日退款笔数趋势 | `refund_daily_trend` |
| 商户交易金额排名 | `merchant_payment_rank` |
| 支付订单平均金额 | `payment_amount_average` (dynamic entity query, `AVG(amount)`) |

Unmapped questions (e.g.「火星基地飞船泊位能耗」) are rejected with a clear reason instead of generating unsafe SQL.

### Dynamic entity aggregations

Beyond the fixed SQL templates, `template: dynamic_entity_query` intents route through a small compiler that accepts `sum` / `avg` / `max` / `min` (plus the default `count`) against columns declared as `metric_fields` on the entity. Anything else — an unknown aggregation, or a column outside the entity's `metric_fields` — is rejected upstream (`entity_metric_not_allowed` / `entity_metric_field_not_allowed`) so the SQL guard never sees it. Declare metric fields in `configs/business_semantics.yaml` under `entity_query_schemas.<entity>.metric_fields`; see the `payment_order.amount` entry in the demo config.

## Project layout

```
text2sql-mvp/
├── apps/api/              # FastAPI HTTP service
├── apps/mcp/              # MCP tool server
├── packages/text2sql_runtime/  # Core runtime
├── configs/               # Whitelist + business semantics (customize here)
├── eval_cases/            # Dry-run regression cases
├── scripts/               # Schema introspection, eval runner
├── tests/
└── docs/
```

## Configuration

Copy environment template (keep secrets in `.env.local`, never commit it):

```bash
cp .env.example .env.local
```

See [SECURITY.md](SECURITY.md) for what must stay local-only.

Key files:

| File | Purpose |
|------|---------|
| `configs/whitelist_tables.yaml` | Physical schema allowlist |
| `configs/business_semantics.yaml` | Intents + SQL templates |
| `configs/semantic_overrides.yaml` | Keyword concepts, sensitive terms |
| `configs/performance.yaml` | EXPLAIN / cost limits |

See [docs/schema-generation.md](docs/schema-generation.md) for generating a whitelist from MySQL.

## API

```bash
curl -s http://127.0.0.1:8777/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"支付订单按渠道统计","domainId":"demo-tenant-1","allowReturnSql":true}'
```

`domainId` is injected as the tenant filter (`tenant_id = %(domain_id)s` in demo schema).

## MCP

```bash
PYTHONPATH=apps/mcp/src:apps/api/src:packages/text2sql_runtime/src \
  python -m text2sql_mcp.server
```

Or after install: `text2sql-mcp`

Details: [docs/mcp-integration.md](docs/mcp-integration.md)

## Tests & evals

```bash
pytest
PYTHONPATH=apps/api/src:packages/text2sql_runtime/src python scripts/run_evals.py
```

## Architecture

See [docs/architecture.md](docs/architecture.md).

## Tutorial (中文)

New to Text2SQL? Read [docs/text2sql-intro.md](docs/text2sql-intro.md) — a beginner-friendly walkthrough using the payment demo, with a **where to change what** guide aligned to this repo.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
