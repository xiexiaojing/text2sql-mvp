# Repository Guidelines

## Project Structure & Module Organization
This repo is a Python 3.11 monorepo with three `src/`-layout packages wired together in `pyproject.toml`:

| Tier | Directory | Role |
|------|-----------|------|
| HTTP API | `apps/api/src/text2sql_api/` | FastAPI endpoints + built-in chat UI (`/chat`) |
| MCP Server | `apps/mcp/src/text2sql_mcp/` | MCP tools exposing the same runtime |
| Core Runtime | `packages/text2sql_runtime/src/text2sql_runtime/` | All business logic (31 modules) |
| Config | `configs/` | YAML files that drive runtime behavior declaratively |
| Eval | `eval_cases/` | Regression test cases |
| Tests | `tests/` | Pytest test suite (15 files) |
| Scripts | `scripts/` | Schema introspection, JPA extraction, eval runner |


## Building Services and Access
Python 3.11 or above and Git Bash command-line tool must be installed on the Windows system.

**Start/Stop Service:**
```
- Linux/macOS: `./start.sh` / `./stop.sh`
- Windows（Git Bash）: `bash winKaiShi.sh` / `bash winTingZhi.sh`
```
**Visit page:**
- http://127.0.0.1:8777/chat
- **Default Dry run**: No database connection by default, only testing whether the service starts successfully. During development, it is necessary to copy '. env. example' to '. env. local' to store the local key (do not submit keys or credentials to repositories such as GitHub). To switch, set 'TEXT2VNet EXECUTIONMODE=live' in '. env. local' and provide MySQL connection information. The MySQL test case can be found in scripts/creat_tables. SQL.


## Architecture & Request Flow (Defense-in-Depth)

Every natural language question passes through a layered safety pipeline:

```
Question
 → Conversation Rewriting (follow-up contextualization: chart switches, dimension changes)
 → Business Semantics Router (multi-stage intent matching)
    ├─ Exact example match (fast path, skips LLM)
    ├─ Lexical match (match.all / match.any / match.none rules)
    ├─ Vector similarity (optional, via OpenAI-compatible embeddings)
    └─ LLM slot extraction (controlled by TEXT2SQL_LLM_SLOT_POLICY)
 → Semantic Plan result:
    ├─ executable template → SQL Template Compiler (parameterized, no LLM needed)
    ├─ guarded_text2sql   → Schema-Driven or LLM SQL Generator (see below)
    ├─ needs_mapping       → Reject: "此问题暂未配置"
    └─ metadata            → Field explanation path
 → SQL Policy: inject `tenant_id = %(domain_id)s` + ensure LIMIT
 → SQL Guard: 9 AST-level checks (single SELECT, no DML/DDL/subqueries, tables/columns/joins/functions whitelisted)
 → EXPLAIN cost check: reject if scan_rows > 500K or >6 tables
 → Executor: DryRun (default) or Live MySQL (PyMySQL read-only or MCP delegation)
 → Formatter: human-readable answer + table + optional ECharts visualization (12+ chart types)
 → Audit: SQLite audit log of every query
```

**Core design principles:**
- **Whitelist-first**: Only tables/columns/joins listed in `configs/whitelist_tables.yaml` are reachable at runtime.
- **Semantics-first**: Known business questions match pre-reviewed SQL templates before any LLM fallback.
- **Dual generation path**: When semantics can't match a template but the intent is recognized (`guarded_text2sql`), the system chooses between `SchemaDrivenSqlGenerator` (rule-based, infers query shape from keywords + BFS join resolution) and `OpenAICompatibleSqlGenerator` (LLM-based, supports both OpenAI and Anthropic transports). The router selects based on intent confidence and `TEXT2SQL_LLM_SLOT_POLICY`.

## Key Module Responsibilities

The central orchestrator is `service.py` (725L) — `Text2SqlService` assembles all components and defines the query pipeline end-to-end.

| Module | Role |
|--------|------|
| `business_semantics.py` (1344L) | Intent matching engine with multi-stage matching, SQL template compilation with slot binding |
| `generator.py` | `SchemaDrivenSqlGenerator` (rule-based) + `OpenAICompatibleSqlGenerator` (LLM-based with JSON response parsing) |
| `sql_guard.py` | Validates generated SQL against whitelist — tables, columns, joins, functions, domain filter, no sensitive columns |
| `sql_policy.py` | Injects `WHERE tenant_id = %(domain_id)s` for scoped tables; adds/caps LIMIT |
| `executor.py` | Three backends: `DryRunExecutor`, `MySqlReadOnlyExecutor` (PyMySQL), `MySqlMcpExecutor` (MCP delegation) |
| `visualization.py` (772L) | Builds ECharts JSON options for pie/bar/line/radar/rose/funnel/sankey/waterfall/scatter/radar/heatmap/funnel |
| `conversation.py` | Follow-up handling: chart-type switches (pie→line), dimension changes ("那按状态呢"), subject inheritance from history |
| `formatter.py` | Generates human-readable answers + structured table data, scalar count templates, entity label mapping |
| `audit.py` | `SQLiteAuditStore` — records every query with question, SQL, status, timing, EXPLAIN, interaction logs |
| `context.py` | Builds LLM prompt context from semantic concepts, candidate tables, conversation history |
| `config.py` | `RuntimeSettings` — reads all config from environment variables (`.env.local`) and `configs/performance.yaml` |
| `router.py` | Question policy validation (no export/cross-domain), table estimation, EXPLAIN-based rejection thresholds |

## Configuration Files (Declarative System Brain)

These YAML files drive runtime behavior — changing them changes the system without code changes:

| File | Feeds Into | Effect |
|------|-----------|--------|
| `configs/whitelist_tables.yaml` | `SchemaCatalog` → `SqlGuard`, `SqlPolicy`, `SchemaContextBuilder`, `Router` | Defines allowed tables, columns, indexes, join paths, domain column, row counts |
| `configs/business_semantics.yaml` | `BusinessSemanticIndex` | Defines intents (match rules + examples), SQL templates, entity mappings, output types |
| `configs/semantic_overrides.yaml` | `SemanticIndex` → `Router`, `SchemaContextBuilder` | Lightweight keyword-based concept detection (3 concepts in demo) |
| `configs/performance.yaml` | `Router`, `SqlGuard`, `SqlPolicy` | Timeouts (5s preflight, 30s hard kill), row limits (200 default, 1000 max), allowed SQL functions, EXPLAIN thresholds |

**When adding new business metrics:** update `business_semantics.yaml` first (add intent + SQL template with `{domain_id}`, `{result_limit}` slots), then `whitelist_tables.yaml` if new tables/columns are needed. Use `scripts/introspect_schema.py` to generate a whitelist from a live MySQL database's `information_schema`.



## Test, and Development Commands

**Foreground API server:** `./scripts/start_api.sh` runs uvicorn directly on port 8777.

**Run tests:** `pytest` (defaults to dry_run mode, no database needed). Use `pytest -m "not live"` to explicitly skip live-DB tests.

**Run eval regressions:** `PYTHONPATH=apps/api/src:packages/text2sql_runtime/src python scripts/run_evals.py`

**Generate schema from MySQL:** `python scripts/introspect_schema.py --host ... --user ... --database ... --output configs/whitelist_tables.yaml`

## Eval Cases

Regression cases live in `eval_cases/cases.yaml`. Each case specifies a question, expected tables touched, expected features, and whether rejection is expected. Cases are run via `scripts/run_evals.py`, which loads `Text2SqlService` in dry_run mode, processes each case, and outputs JSON results comparing actual vs expected behavior. Add new cases when adding intents or changing SQL templates.

## Coding Style & Naming Conventions
Use 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and keep files in the existing package layout. Formatting follows `ruff` and `black` settings in `pyproject.toml`: 100-character line length, Python 3.11 target, and import sorting via `ruff`. Prefer small, explicit functions over dense helper chains.

## Testing Guidelines
`pytest` is the test runner. Test files follow `tests/test_*.py`, and shared fixtures live in `tests/conftest.py`. The `conftest.py` provides a `service` fixture with `Text2SqlService` in `dry_run` mode + `allow_sensitive_fields=True`. Use the `@pytest.mark.live` marker only for tests that require a real MySQL read-only database. Add or update tests whenever SQL guard, routing, formatter, or config behavior changes.

## Commit & Pull Request Guidelines
Recent commits use conventional prefixes such as `feat(...)`, `chore:`, and `fix:`. Keep commits focused and descriptive. PRs should explain the behavioral change, list config files touched when relevant, and include test or eval results. Add screenshots only for UI changes.

## Security & Configuration Tips
Copy `.env.example` to `.env.local` for local secrets. Do not commit secrets or live credentials. Review `SECURITY.md` before changing database, API key, or tenant-handling logic. Key env vars: `TEXT2SQL_LLM_API_KEY`, `TEXT2SQL_LLM_BASE_URL`, `TEXT2SQL_LLM_MODEL` (for LLM features), `TEXT2SQL_DB_HOST` / `TEXT2SQL_DB_USER` / `TEXT2SQL_DB_PASSWORD` / `TEXT2SQL_DB_NAME` (for live MySQL mode).
