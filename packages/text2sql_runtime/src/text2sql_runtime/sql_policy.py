from __future__ import annotations

import re

from .models import TableRef
from .schema import SchemaCatalog
from .sql_utils import extract_table_refs, has_where, normalize_sql, split_before_suffix

LIMIT_RE = re.compile(r"\blimit\s+(\d+)\b", re.IGNORECASE)
AGGREGATE_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
GROUP_BY_RE = re.compile(r"\bgroup\s+by\b", re.IGNORECASE)


def inject_domain_filter(sql: str, catalog: SchemaCatalog, domain_id: str) -> tuple[str, dict[str, str]]:
    normalized = normalize_sql(sql)
    refs = extract_table_refs(normalized)
    predicates = _missing_domain_predicates(normalized, refs, catalog)
    if not predicates:
        return normalized, {"domain_id": domain_id}

    predicate_sql = " AND ".join(predicates)
    prefix, suffix = split_before_suffix(normalized)
    if has_where(prefix):
        prefix = f"{prefix} AND {predicate_sql}"
    else:
        prefix = f"{prefix} WHERE {predicate_sql}"
    return " ".join(part for part in [prefix, suffix] if part).strip(), {"domain_id": domain_id}


def ensure_limit(sql: str, default_limit: int, max_limit: int) -> str:
    normalized = normalize_sql(sql)
    if _is_scalar_aggregate(normalized):
        return normalized
    match = LIMIT_RE.search(normalized)
    if match:
        requested = int(match.group(1))
        if requested <= max_limit:
            return normalized
        return LIMIT_RE.sub(f"LIMIT {max_limit}", normalized, count=1)
    return f"{normalized} LIMIT {default_limit}"


def _is_scalar_aggregate(sql: str) -> bool:
    return bool(AGGREGATE_RE.search(sql)) and GROUP_BY_RE.search(sql) is None


def _missing_domain_predicates(
    sql: str, refs: list[TableRef], catalog: SchemaCatalog
) -> list[str]:
    predicates: list[str] = []
    for ref in refs:
        table = catalog.get(ref.table)
        if not table or not table.domain_column:
            continue
        qualifier = re.escape(ref.alias or ref.table)
        column = re.escape(table.domain_column)
        if has_domain_filter(sql, qualifier, column):
            continue
        predicates.append(f"{ref.alias}.{table.domain_column} = %(domain_id)s")
    return predicates


def has_domain_filter(sql: str, qualifier: str, column: str) -> bool:
    qualified_column = rf"\b{qualifier}\s*\.\s*`?{column}`?\b"
    placeholder = r"%\(\s*domain_id\s*\)s"
    return bool(
        re.search(rf"{qualified_column}\s*=\s*{placeholder}", sql, re.IGNORECASE)
        or re.search(rf"{placeholder}\s*=\s*{qualified_column}", sql, re.IGNORECASE)
    )
