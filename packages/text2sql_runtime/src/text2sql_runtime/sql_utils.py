from __future__ import annotations

import re

from .models import TableRef

COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
COMMENT_LINE_RE = re.compile(r"(--[^\n]*|#[^\n]*)")
STRING_RE = re.compile(r"'(?:''|[^'])*'")
TABLE_REF_RE = re.compile(
    r"\b(from|join)\s+`?([A-Za-z_][A-Za-z0-9_]*)`?"
    r"(?:\s+(?:as\s+)?`?([A-Za-z_][A-Za-z0-9_]*)`?)?",
    re.IGNORECASE,
)
SQL_CLAUSE_RE = re.compile(r"\b(group\s+by|order\s+by|limit)\b", re.IGNORECASE)
WHERE_RE = re.compile(r"\bwhere\b", re.IGNORECASE)

RESERVED_AFTER_TABLE = {
    "where",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "on",
    "group",
    "order",
    "limit",
    "having",
}


def strip_comments(sql: str) -> str:
    sql = COMMENT_BLOCK_RE.sub(" ", sql)
    return COMMENT_LINE_RE.sub(" ", sql)


def normalize_sql(sql: str) -> str:
    cleaned = strip_comments(sql).strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    return re.sub(r"\s+", " ", cleaned)


def remove_string_literals(sql: str) -> str:
    return STRING_RE.sub("''", sql)


def extract_table_refs(sql: str) -> list[TableRef]:
    refs: list[TableRef] = []
    for match in TABLE_REF_RE.finditer(remove_string_literals(sql)):
        table = match.group(2)
        alias = match.group(3) or table
        if alias.lower() in RESERVED_AFTER_TABLE:
            alias = table
        refs.append(TableRef(table=table, alias=alias))
    return refs


def first_suffix_clause(sql: str) -> re.Match[str] | None:
    matches = list(SQL_CLAUSE_RE.finditer(sql))
    return min(matches, key=lambda match: match.start()) if matches else None


def split_before_suffix(sql: str) -> tuple[str, str]:
    match = first_suffix_clause(sql)
    if not match:
        return sql, ""
    return sql[: match.start()].rstrip(), sql[match.start() :].lstrip()


def has_where(sql: str) -> bool:
    prefix, _ = split_before_suffix(sql)
    return WHERE_RE.search(prefix) is not None
