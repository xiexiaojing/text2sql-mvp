from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Text2SqlError(Exception):
    """Base runtime error."""


class RejectedQuery(Text2SqlError):
    def __init__(self, reason: str, code: str = "rejected") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    display_name: str | None = None
    data_type: str | None = None
    sensitive: bool = False
    searchable: bool = True


@dataclass(frozen=True)
class JoinPath:
    to_table: str
    on: str


@dataclass(frozen=True)
class TableSchema:
    name: str
    object_name: str
    display_name: str
    domain_column: str | None
    columns: dict[str, ColumnSchema]
    source_class: str | None = None
    row_count_estimate: int = 0
    indexes: list[dict[str, Any]] = field(default_factory=list)
    joins: list[JoinPath] = field(default_factory=list)

    def has_column(self, column: str) -> bool:
        return column.lower() in {name.lower() for name in self.columns}

    def column(self, column: str) -> ColumnSchema | None:
        lowered = column.lower()
        for name, schema in self.columns.items():
            if name.lower() == lowered:
                return schema
        return None


@dataclass(frozen=True)
class TableRef:
    table: str
    alias: str


@dataclass(frozen=True)
class QueryInput:
    question: str
    domain_id: str
    history: list[dict[str, Any]] = field(default_factory=list)
    user_id: str | None = None
    allow_return_sql: bool = False
    max_rows: int | None = None
    force_llm: bool = False


@dataclass(frozen=True)
class EstimateResult:
    status: str
    hit_path: str
    estimated_seconds: float
    candidate_tables: list[str]
    rejection_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    semantic_plan: dict[str, Any] | None = None


@dataclass(frozen=True)
class GeneratedSql:
    sql: str
    plan: str
    hit_path: str
    params: dict[str, Any] = field(default_factory=dict)
    interaction_logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    explain: list[dict[str, Any]]
    elapsed_ms: int
    scanned_rows: int
    mode: str


@dataclass(frozen=True)
class QueryResult:
    query_id: str
    status: str
    answer: str | None
    table: dict[str, Any] | None
    generated_sql: str | None
    elapsed_ms: int
    hit_path: str
    rejection_reason: str | None = None
    explain: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    interaction_logs: list[dict[str, Any]] = field(default_factory=list)
    semantic_plan: dict[str, Any] | None = None
    echarts_option: dict[str, Any] | None = None
