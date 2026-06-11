from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP
from text2sql_runtime.models import QueryInput
from text2sql_runtime.service import Text2SqlService

mcp = FastMCP("text2sql-mvp")


@lru_cache(maxsize=1)
def service() -> Text2SqlService:
    return Text2SqlService.from_project_root()


@mcp.tool()
def health() -> dict[str, str]:
    """Return runtime mode and LLM configuration status."""
    current_service = service()
    return {
        "status": "ok",
        "executionMode": "live" if current_service.settings.live_execution else "dry_run",
        "executorBackend": current_service.settings.executor_backend,
        "allowSensitiveFields": str(current_service.settings.allow_sensitive_fields).lower(),
        "llm": "configured" if current_service.settings.llm.configured else "fallback",
        "llmPolicy": current_service.settings.llm.policy,
    }


@mcp.tool()
def query(
    question: str,
    domain_id: str,
    user_id: str = "mcp",
    allow_return_sql: bool = False,
    force_llm: bool = False,
    max_rows: int | None = None,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run a guarded natural-language Text2SQL query.

    The tool does not accept raw SQL. It delegates to the same runtime path used by
    the HTTP API: routing, schema context selection, SQL generation, domain_id
    injection, AST validation, EXPLAIN cost checks, read-only execution, and audit.
    """
    return _model_dump(
        service().query(
            QueryInput(
                question=_required_text(question, "question"),
                domain_id=_required_text(domain_id, "domain_id"),
                history=history or [],
                user_id=user_id or "mcp",
                allow_return_sql=allow_return_sql,
                force_llm=force_llm,
                max_rows=_validate_max_rows(max_rows),
            )
        )
    )


@mcp.tool()
def estimate(
    question: str,
    domain_id: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Estimate query complexity and rejection status without executing SQL."""
    return _model_dump(
        service().estimate(
            _required_text(question, "question"),
            _required_text(domain_id, "domain_id"),
            history or [],
        )
    )


@mcp.tool()
def schema_summary() -> dict[str, Any]:
    """Return the whitelisted schema, semantic concepts, and execution mode."""
    return service().schema_summary()


@mcp.resource("text2sql://schema/summary")
def schema_summary_resource() -> str:
    """Expose schema summary as an MCP resource for clients that prefer resources."""
    return json.dumps(schema_summary(), ensure_ascii=False)


@mcp.tool()
def audit(query_id: str) -> dict[str, Any]:
    """Return an audit record for a previous query."""
    record = service().audit(_required_text(query_id, "query_id"))
    if record is None:
        raise ValueError("query_id not found")
    return record


@mcp.tool()
def unsupported_questions(limit: int = 50, since_ms: int | None = None) -> dict[str, Any]:
    """Collect questions rejected because no business semantic metric is configured."""
    return service().unsupported_questions(
        limit=_validate_collect_limit(limit),
        since_ms=_validate_since_ms(since_ms),
    )


def main() -> None:
    mcp.run()


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        result: dict[str, Any] = {}
        for key in value.__dataclass_fields__:
            result[_camel_case(key)] = getattr(value, key)
        return result
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    return dict(value)


def _camel_case(value: str) -> str:
    chunks = value.split("_")
    return chunks[0] + "".join(chunk.capitalize() for chunk in chunks[1:])


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing required field: {field}")
    return value.strip()


def _validate_max_rows(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1 or parsed > 1000:
        raise ValueError("max_rows must be between 1 and 1000")
    return parsed


def _validate_collect_limit(value: int) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 500:
        raise ValueError("limit must be between 1 and 500")
    return parsed


def _validate_since_ms(value: int | None) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError("since_ms must be non-negative")
    return parsed


if __name__ == "__main__":
    main()
