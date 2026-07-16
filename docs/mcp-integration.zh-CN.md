# MCP 集成

MCP 服务端暴露了与 HTTP API 相同的受保护运行时。

## 流程

```
MCP 客户端 -> text2sql query 工具 -> Text2SqlService -> 受保护的 SQL -> mysql（live）或 dry_run
```

## 工具

| 工具 | 说明 |
|------|------|
| `health` | 运行时模式和 LLM 状态 |
| `query` | 自然语言查询（不接受原始 SQL） |
| `estimate` | 估算查询计划复杂度，不实际执行 |
| `schema_summary` | 白名单和意图目录摘要 |
| `audit` | 按查询 ID 获取审计记录 |
| `unsupported_questions` | 最近被拒绝或未配置的问题 |

## 资源

```
text2sql://schema/summary
```

## 本地运行

```bash
PYTHONPATH=apps/mcp/src:apps/api/src:packages/text2sql_runtime/src \
  python -m text2sql_mcp.server
```

通过 `pip install -e .` 安装后：

```bash
text2sql-mcp
```

## Cursor / Claude Desktop 配置示例

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

## 通过 MCP 执行器连接 MySQL

在 `.env.local` 中设置：

```bash
TEXT2SQL_EXECUTOR_BACKEND=mysql_mcp
TEXT2SQL_MYSQL_MCP_COMMAND=python
TEXT2SQL_MYSQL_MCP_ARGS=-m text2sql_mcp.mysql_mcp_compat
```

`text2sql_mcp.mysql_mcp_compat` 封装了 `mcp-mysql-server`，可在需要时设置 `ssl_disabled=True`。
