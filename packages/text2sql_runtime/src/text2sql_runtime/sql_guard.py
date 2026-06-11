from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import RejectedQuery, TableRef
from .schema import SchemaCatalog
from .sql_policy import AGGREGATE_RE, GROUP_BY_RE, LIMIT_RE, has_domain_filter
from .sql_utils import extract_table_refs, normalize_sql, remove_string_literals

FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|replace|merge|call|grant|revoke|"
    r"load|lock|unlock|use|show|set|handler|optimize|repair)\b|"
    r"\binto\s+(outfile|dumpfile)\b",
    re.IGNORECASE,
)
QUALIFIED_RE = re.compile(
    r"`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\.\s*`?([A-Za-z_][A-Za-z0-9_]*)`?"
)
FUNCTION_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE)
IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
AS_ALIAS_RE = re.compile(r"\bas\s+`?([A-Za-z_][A-Za-z0-9_]*)`?", re.IGNORECASE)
PARAM_PLACEHOLDER_RE = re.compile(r"%\(\s*[A-Za-z_][A-Za-z0-9_]*\s*\)s")

SQL_KEYWORDS = {
    "select",
    "from",
    "where",
    "and",
    "or",
    "not",
    "null",
    "is",
    "in",
    "between",
    "like",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "full",
    "cross",
    "on",
    "group",
    "by",
    "order",
    "having",
    "limit",
    "offset",
    "asc",
    "desc",
    "as",
    "case",
    "when",
    "then",
    "else",
    "end",
    "distinct",
    "true",
    "false",
}


@dataclass(frozen=True)
class GuardResult:
    sql: str
    tables: list[str]
    warnings: list[str] = field(default_factory=list)


class SqlGuard:
    def __init__(
        self,
        catalog: SchemaCatalog,
        allowed_functions: list[str],
        require_domain_filter: bool = True,
        allow_sensitive_fields: bool = False,
    ) -> None:
        self.catalog = catalog
        self.allowed_functions = {item.lower() for item in allowed_functions}
        self.require_domain_filter = require_domain_filter
        self.allow_sensitive_fields = allow_sensitive_fields

    def validate(self, sql: str) -> GuardResult:
        normalized = normalize_sql(sql)
        self._validate_single_select(normalized)
        refs = extract_table_refs(normalized)
        if not refs:
            raise RejectedQuery("SQL 未包含可识别的 FROM 表", "no_table")
        self._validate_tables(refs)
        self._validate_join_paths(refs)
        self._validate_functions(normalized)
        self._validate_columns(normalized, refs)
        if self.require_domain_filter:
            self._validate_domain_filter(normalized, refs)
        self._validate_limit(normalized)
        return GuardResult(sql=normalized, tables=[ref.table.lower() for ref in refs])

    def _validate_single_select(self, sql: str) -> None:
        self._validate_with_optional_ast(sql)
        if ";" in sql:
            raise RejectedQuery("禁止多语句 SQL", "multi_statement")
        if not re.match(r"^\s*select\b", sql, re.IGNORECASE):
            raise RejectedQuery("只允许 SELECT 查询", "not_select")
        if FORBIDDEN_RE.search(remove_string_literals(sql)):
            raise RejectedQuery("SQL 包含禁止的写操作、DDL 或会话操作", "forbidden_sql")
        if re.search(r"\bfrom\s*\(", remove_string_literals(sql), re.IGNORECASE):
            raise RejectedQuery("第一版不允许子查询，请改为受控 join 或预聚合能力", "subquery")

    def _validate_with_optional_ast(self, sql: str) -> None:
        try:
            import sqlglot
            from sqlglot import expressions as exp
        except ModuleNotFoundError:
            return
        try:
            parsed = sqlglot.parse(sql, read="mysql")
        except Exception as exc:
            raise RejectedQuery(f"SQL AST 解析失败: {exc}", "sql_parse_failed") from exc
        if len(parsed) != 1:
            raise RejectedQuery("禁止多语句 SQL", "multi_statement")
        if not isinstance(parsed[0], exp.Select):
            raise RejectedQuery("只允许 SELECT 查询", "not_select")

    def _validate_tables(self, refs: list[TableRef]) -> None:
        for ref in refs:
            table_name = ref.table.lower()
            if table_name.endswith("_standard_history"):
                raise RejectedQuery("禁止查询审计历史表 *_standard_history", "history_table")
            if self.catalog.get(table_name) is None:
                raise RejectedQuery(f"表不在白名单中: {ref.table}", "table_not_allowed")

    def _validate_join_paths(self, refs: list[TableRef]) -> None:
        if len(refs) <= 1:
            return
        allowed_pairs = self.catalog.allowed_join_pairs()
        previous = refs[0].table.lower()
        for ref in refs[1:]:
            current = ref.table.lower()
            if frozenset({previous, current}) not in allowed_pairs:
                raise RejectedQuery(f"join 路径未白名单审核: {previous} -> {current}", "join_not_allowed")
            previous = current

    def _validate_functions(self, sql: str) -> None:
        without_strings = _remove_param_placeholders(remove_string_literals(sql))
        for function_name in FUNCTION_RE.findall(without_strings):
            lowered = function_name.lower()
            if lowered in SQL_KEYWORDS:
                continue
            if lowered not in self.allowed_functions:
                raise RejectedQuery(f"函数不在白名单中: {function_name}", "function_not_allowed")

    def _validate_columns(self, sql: str, refs: list[TableRef]) -> None:
        without_strings = _remove_param_placeholders(remove_string_literals(sql))
        alias_to_table = {ref.alias.lower(): ref.table.lower() for ref in refs}
        alias_to_table.update({ref.table.lower(): ref.table.lower() for ref in refs})
        referenced_tables = [ref.table.lower() for ref in refs]
        sensitive_columns: set[str] = set()

        for qualifier, column in QUALIFIED_RE.findall(without_strings):
            table_name = alias_to_table.get(qualifier.lower())
            if not table_name:
                raise RejectedQuery(f"未知表别名: {qualifier}", "unknown_alias")
            column_schema = self.catalog.require(table_name).column(column)
            if column_schema is None:
                raise RejectedQuery(f"字段不在白名单中: {qualifier}.{column}", "column_not_allowed")
            if not self.allow_sensitive_fields and (
                column_schema.sensitive or not column_schema.searchable
            ):
                sensitive_columns.add(f"{qualifier}.{column}")

        output_aliases = {item.lower() for item in AS_ALIAS_RE.findall(without_strings)}
        known_columns = self.catalog.column_names_for_tables(referenced_tables)
        known_tokens = set(SQL_KEYWORDS)
        known_tokens.update(alias_to_table)
        known_tokens.update(output_aliases)
        known_tokens.update({"domain_id", "s"})
        known_tokens.update(self.allowed_functions)
        for token in IDENTIFIER_RE.findall(_remove_qualified_identifiers(without_strings)):
            lowered = token.lower()
            if lowered in known_tokens:
                continue
            if lowered in known_columns:
                column_schema = _find_column(self.catalog, referenced_tables, token)
                if (
                    not self.allow_sensitive_fields
                    and column_schema
                    and (column_schema.sensitive or not column_schema.searchable)
                ):
                    sensitive_columns.add(token)
                continue
            raise RejectedQuery(f"字段不在白名单中: {token}", "column_not_allowed")

        if sensitive_columns:
            joined = ", ".join(sorted(sensitive_columns))
            raise RejectedQuery(f"第一版不允许使用敏感字段检索或返回: {joined}", "sensitive_column")

    def _validate_domain_filter(self, sql: str, refs: list[TableRef]) -> None:
        without_strings = remove_string_literals(sql)
        for ref in refs:
            table = self.catalog.require(ref.table)
            if not table.domain_column:
                continue
            qualifier = re.escape(ref.alias)
            column = re.escape(table.domain_column)
            if not has_domain_filter(without_strings, qualifier, column):
                raise RejectedQuery(f"缺少 domainId 权限过滤: {ref.alias}.{table.domain_column}", "missing_domain_filter")

    def _validate_limit(self, sql: str) -> None:
        if AGGREGATE_RE.search(sql) and GROUP_BY_RE.search(sql) is None:
            return
        if not LIMIT_RE.search(sql):
            raise RejectedQuery("明细或分组查询必须包含 LIMIT", "missing_limit")


def _remove_qualified_identifiers(sql: str) -> str:
    return QUALIFIED_RE.sub(" ", sql)


def _remove_param_placeholders(sql: str) -> str:
    return PARAM_PLACEHOLDER_RE.sub(" ", sql)


def _find_column(catalog: SchemaCatalog, table_names: list[str], column: str):
    for table_name in table_names:
        table = catalog.get(table_name)
        if not table:
            continue
        column_schema = table.column(column)
        if column_schema:
            return column_schema
    return None
