# MCP Integration

The MCP server exposes the same guarded runtime as the HTTP API.

## Flow

```
MCP client -> text2sql query tool -> Text2SqlService -> guarded SQL -> mysql (live) or dry_run
```

## Tools

| Tool | Description |
|------|-------------|
| `health` | Runtime mode and LLM status |
| `query` | Natural language query (no raw SQL input) |
| `estimate` | Plan complexity without execution |
| `schema_summary` | Whitelist + intent catalog |
| `audit` | Fetch audit record by query id |
| `unsupported_questions` | Recent rejected/unconfigured questions |

## Resource

```
text2sql://schema/summary
```

## Run locally

```bash
PYTHONPATH=apps/mcp/src:apps/api/src:packages/text2sql_runtime/src \
  python -m text2sql_mcp.server
```

After `pip install -e .`:

```bash
text2sql-mcp
```

## Cursor / Claude Desktop example

```json
{
  "mcpServers": {
    "text2sql-mvp": {
      "command": "text2sql-mcp",
      "env": {
        "TEXT2SQL_PROJECT_ROOT": "/absolute/path/to/text2sql-mvp",
        "TEXT2SQL_EXECUTION_MODE": "dry_run"
      }
    }
  }
}
```

## MySQL via MCP executor

Set in `.env.local`:

```bash
TEXT2SQL_EXECUTOR_BACKEND=mysql_mcp
TEXT2SQL_MYSQL_MCP_COMMAND=python
TEXT2SQL_MYSQL_MCP_ARGS=-m text2sql_mcp.mysql_mcp_compat
```

`text2sql_mcp.mysql_mcp_compat` wraps `mcp-mysql-server` and can set `ssl_disabled=True` when needed.
