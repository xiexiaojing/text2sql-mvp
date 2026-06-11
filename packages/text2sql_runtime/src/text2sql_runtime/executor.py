from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import threading
import time
from typing import Any, Protocol

import pymysql
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pymysql.cursors import DictCursor

from .config import MySqlMcpSettings, MySqlSettings, RuntimeSettings
from .models import ExecutionResult

PARAM_RE = re.compile(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
PYMYSQL_PARAM_TOKEN_RE = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)s")
MYSQL_EXPLAIN_COLUMNS = [
    "id",
    "select_type",
    "table",
    "partitions",
    "type",
    "possible_keys",
    "key",
    "key_len",
    "ref",
    "rows",
    "filtered",
    "Extra",
]


def prepare_pymysql_sql(sql: str) -> str:
    """Escape literal percent signs for PyMySQL's %(name)s interpolation."""
    tokens: list[str] = []

    def stash(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"__PYMYSQL_PARAM_{len(tokens) - 1}__"

    escaped = PYMYSQL_PARAM_TOKEN_RE.sub(stash, sql).replace("%", "%%")
    for index, token in enumerate(tokens):
        escaped = escaped.replace(f"__PYMYSQL_PARAM_{index}__", token)
    return escaped


class SqlExecutor(Protocol):
    def explain(self, sql: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        ...

    def execute(self, sql: str, params: dict[str, Any], max_rows: int) -> ExecutionResult:
        ...


class DryRunExecutor:
    def explain(self, sql: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        del sql, params
        return [], 0

    def execute(self, sql: str, params: dict[str, Any], max_rows: int) -> ExecutionResult:
        del sql, params, max_rows
        return ExecutionResult(
            columns=[],
            rows=[],
            explain=[],
            elapsed_ms=0,
            scanned_rows=0,
            mode="dry_run",
        )


class MySqlReadOnlyExecutor:
    def __init__(self, settings: MySqlSettings) -> None:
        self.settings = settings

    def explain(self, sql: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        with self._connect() as connection:
            explain = self._explain(connection, sql, params)
        return explain, estimate_scanned_rows(explain)

    def execute(self, sql: str, params: dict[str, Any], max_rows: int) -> ExecutionResult:
        start = time.monotonic()
        with self._connect() as connection:
            with connection.cursor(DictCursor) as cursor:
                cursor.execute(prepare_pymysql_sql(sql), params)
                rows = list(cursor.fetchmany(max_rows))
                columns = [item[0] for item in cursor.description or []]
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ExecutionResult(
            columns=columns,
            rows=rows,
            explain=[],
            elapsed_ms=elapsed_ms,
            scanned_rows=0,
            mode="live",
        )

    def _connect(self):
        connect_kwargs: dict[str, Any] = {
            "host": self.settings.host,
            "port": self.settings.port,
            "user": self.settings.user,
            "password": self.settings.password,
            "database": self.settings.database,
            "cursorclass": DictCursor,
            "autocommit": True,
            "connect_timeout": self.settings.connect_timeout_seconds,
            "read_timeout": self.settings.read_timeout_seconds,
            "write_timeout": self.settings.read_timeout_seconds,
            "charset": "utf8mb4",
        }
        if self.settings.ssl_disabled:
            connect_kwargs["ssl_disabled"] = True
        return pymysql.connect(**connect_kwargs)

    def _explain(self, connection, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        with connection.cursor(DictCursor) as cursor:
            cursor.execute(f"EXPLAIN {prepare_pymysql_sql(sql)}", params)
            return list(cursor.fetchall())


class MySqlMcpExecutor:
    def __init__(self, settings: MySqlSettings, mcp_settings: MySqlMcpSettings) -> None:
        self.settings = settings
        self.mcp_settings = mcp_settings

    def explain(self, sql: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        rendered_sql = render_sql_params(sql, params)
        text = self._call_sql(f"EXPLAIN {rendered_sql}")
        _, rows = parse_mcp_table_text(text)
        return rows, estimate_scanned_rows(rows)

    def execute(self, sql: str, params: dict[str, Any], max_rows: int) -> ExecutionResult:
        start = time.monotonic()
        rendered_sql = render_sql_params(sql, params)
        text = self._call_sql(rendered_sql)
        columns, rows = parse_mcp_table_text(text)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ExecutionResult(
            columns=columns,
            rows=rows[:max_rows],
            explain=[],
            elapsed_ms=elapsed_ms,
            scanned_rows=0,
            mode="mcp_live",
        )

    def _call_sql(self, sql: str) -> str:
        return _run_async(self._call_sql_async(sql), timeout_seconds=self.mcp_settings.timeout_seconds)

    async def _call_sql_async(self, sql: str) -> str:
        arguments = {"query": sql}
        if self.mcp_settings.database:
            arguments["database"] = self.mcp_settings.database
        params = StdioServerParameters(
            command=os.path.expanduser(self.mcp_settings.command),
            args=self.mcp_settings.args,
            env=self._server_env(),
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(self._tool_name(), arguments)
        text_parts = [getattr(item, "text", "") for item in result.content]
        text = "\n".join(part for part in text_parts if part).strip()
        if getattr(result, "isError", False):
            raise RuntimeError(text or "MySQL MCP server returned an error")
        if text.lower().startswith("error"):
            raise RuntimeError(text)
        return text

    def _tool_name(self) -> str:
        if self.mcp_settings.connection_name == "default":
            return "execute_sql"
        return f"execute_sql_{self.mcp_settings.connection_name}"

    def _server_env(self) -> dict[str, str]:
        _require_mysql_settings(self.settings)
        database = self.mcp_settings.database or self.settings.database
        payload = {
            "connections": {
                self.mcp_settings.connection_name: {
                    "host": self.settings.host,
                    "port": self.settings.port,
                    "user": self.settings.user,
                    "password": self.settings.password,
                    "databases": [database],
                    "readonly": True,
                }
            }
        }
        env = os.environ.copy()
        env["MYSQL_CONNECTIONS"] = json.dumps(payload, ensure_ascii=False)
        env.setdefault("MYSQL_MCP_SSL_DISABLED", "true")
        env["MYSQL_READONLY"] = "true"
        return env


def build_executor(settings: RuntimeSettings) -> SqlExecutor:
    if settings.live_execution:
        if settings.executor_backend in {"mcp", "mcp_mysql", "mysql_mcp"}:
            return MySqlMcpExecutor(settings.mysql, settings.mysql_mcp)
        return MySqlReadOnlyExecutor(settings.mysql)
    return DryRunExecutor()


def estimate_scanned_rows(explain_rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in explain_rows:
        raw = row.get("rows") or row.get("rows_examined_per_scan") or 0
        try:
            total += int(raw)
        except (TypeError, ValueError):
            continue
    return total


def render_sql_params(sql: str, params: dict[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in params:
            raise ValueError(f"Missing SQL parameter: {key}")
        return sql_literal(params[key])

    return PARAM_RE.sub(replace, sql)


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{escaped}'"


def parse_mcp_table_text(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []
    parsed = list(csv.reader(lines))
    if not parsed:
        return [], []
    columns = [str(item) for item in parsed[0]]
    rows: list[dict[str, Any]] = []
    for raw_row in parsed[1:]:
        if columns == MYSQL_EXPLAIN_COLUMNS and len(raw_row) > len(columns):
            rows.append(_parse_mysql_explain_row(raw_row))
            continue
        values = list(raw_row[: len(columns)])
        if len(values) < len(columns):
            values.extend([""] * (len(columns) - len(values)))
        rows.append({column: _coerce_mcp_value(value) for column, value in zip(columns, values)})
    return columns, rows


def _parse_mysql_explain_row(values: list[str]) -> dict[str, Any]:
    prefix = values[:5]
    suffix = values[-6:]
    possible_keys = ",".join(values[5:-6])
    normalized = prefix + [possible_keys] + suffix
    return {
        column: _coerce_mcp_value(value)
        for column, value in zip(MYSQL_EXPLAIN_COLUMNS, normalized)
    }


def _coerce_mcp_value(value: str) -> Any:
    if value == "None":
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _require_mysql_settings(settings: MySqlSettings) -> None:
    if not settings.configured:
        raise RuntimeError("MySQL MCP executor requires configured MySQL settings")


def _run_async(coro, timeout_seconds: int):
    async def runner():
        return await asyncio.wait_for(coro, timeout=timeout_seconds)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(runner())

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def run_in_thread() -> None:
        try:
            result["value"] = asyncio.run(runner())
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=run_in_thread, daemon=True)
    thread.start()
    thread.join(timeout_seconds + 1)
    if thread.is_alive():
        raise TimeoutError("MySQL MCP call timed out")
    if "value" in error:
        raise error["value"]
    return result.get("value")
