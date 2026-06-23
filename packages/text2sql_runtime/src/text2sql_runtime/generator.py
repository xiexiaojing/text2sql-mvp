from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx

from .config import LlmSettings
from .models import GeneratedSql, RejectedQuery, TableSchema
from .schema import SchemaCatalog
from .semantics import SemanticIndex, epoch_ms_for_age_at_least, is_set_epoch_ms_sql, month_start_epoch_ms


@dataclass(frozen=True)
class JoinStep:
    table: str
    on: str


@dataclass(frozen=True)
class QueryShape:
    kind: str
    primary_table: str
    question: str
    group_table: str | None = None
    group_column: str | None = None
    filters: tuple[str, ...] = ()
    required_tables: tuple[str, ...] = ()


class SchemaDrivenSqlGenerator:
    def __init__(
        self,
        catalog: SchemaCatalog,
        semantics: SemanticIndex,
        allow_sensitive_fields: bool = False,
    ) -> None:
        self.catalog = catalog
        self.semantics = semantics
        self.allow_sensitive_fields = allow_sensitive_fields

    def generate(self, question: str, schema_context: str, candidate_tables: list[str]) -> GeneratedSql:
        del schema_context
        shape = self._infer_shape(question, candidate_tables)
        sql = self._build_sql(shape)
        return GeneratedSql(
            sql=sql,
            plan=self._describe_shape(shape),
            hit_path="schema_driven",
        )

    def _infer_shape(self, question: str, candidate_tables: list[str]) -> QueryShape:
        primary_table = self._primary_table(question, candidate_tables)
        filters, required_tables = self._semantic_constraints(question, primary_table)
        group = self._group_dimension(question, primary_table, candidate_tables)
        if group:
            group_table, group_column = group
            return QueryShape(
                kind="group_count",
                primary_table=primary_table,
                question=question,
                group_table=group_table,
                group_column=group_column,
                filters=filters,
                required_tables=required_tables,
            )
        if self._is_detail_question(question):
            return QueryShape(
                kind="detail",
                primary_table=primary_table,
                question=question,
                filters=filters,
                required_tables=required_tables,
            )
        return QueryShape(
            kind="count",
            primary_table=primary_table,
            question=question,
            filters=filters,
            required_tables=required_tables,
        )

    def _primary_table(self, question: str, candidate_tables: list[str]) -> str:
        if any(keyword in question for keyword in ["退款", "refund"]) and "refund_order" in self.catalog.table_names:
            return "refund_order"
        if any(keyword in question for keyword in ["商户", "商家", "merchant"]) and "merchant" in self.catalog.table_names:
            if "排名" in question or "排行" in question:
                return "merchant"
        if any(keyword in question for keyword in ["订单", "支付", "payment"]) and "payment_order" in self.catalog.table_names:
            return "payment_order"
        ranked = sorted(
            {table for table in candidate_tables if self.catalog.get(table)},
            key=lambda table: self._table_score(question, self.catalog.require(table)),
            reverse=True,
        )
        if ranked:
            return ranked[0]
        raise RejectedQuery("无法从 schema 中匹配候选表", "no_candidate_table")

    def _table_score(self, question: str, table: TableSchema) -> int:
        score = 0
        tokens = [table.name, table.object_name, table.display_name]
        if any(str(token).lower() in question.lower() for token in tokens):
            score += 5
        for column in table.columns.values():
            if column.display_name and column.display_name in question:
                score += 1
            if column.name.lower() in question.lower():
                score += 1
        if table.name in {"payment_order", "refund_order", "merchant"}:
            score += 1
        return score

    def _semantic_constraints(self, question: str, primary_table: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        filters: list[str] = []
        required_tables: list[str] = []
        age_rules: dict[tuple[str, str], int] = {}
        sensitive_filter = self._sensitive_exact_filter(question, primary_table)
        if sensitive_filter:
            filters.append(sensitive_filter)
        for concept in self.semantics.detect(question):
            rule = concept.rule
            rule_type = rule.get("type")
            if rule_type == "age_at_least":
                table = str(rule.get("table", primary_table))
                column = str(rule.get("column", "born_at"))
                if self.catalog.get(table) and self.catalog.require(table).column(column):
                    key = (table, column)
                    age_rules[key] = max(age_rules.get(key, 0), int(rule["age"]))
            elif rule_type == "table_count" and rule.get("table"):
                continue
            elif rule_type == "tag_name":
                tag_table = str(rule["table"])
                relation_table = str(rule["relation_table"])
                required_tables.extend([relation_table, tag_table])
                filters.append(f"{self._alias(tag_table)}.name = '{rule['value']}'")
        if "本月" in question:
            table = self.catalog.require(primary_table)
            if table.column("create_time"):
                filters.append(f"{self._alias(primary_table)}.create_time >= {month_start_epoch_ms()}")
        for (table, column), age in age_rules.items():
            alias = self._alias(table)
            column_ref = f"{alias}.{column}"
            filters.append(
                f"{is_set_epoch_ms_sql(column_ref)} AND {column_ref} <= {epoch_ms_for_age_at_least(age)}"
            )
        return tuple(filters), tuple(dict.fromkeys(required_tables))

    def _sensitive_exact_filter(self, question: str, primary_table: str) -> str | None:
        if not self.allow_sensitive_fields:
            return None
        table = self.catalog.get(primary_table)
        if not table:
            return None
        alias = self._alias(primary_table)
        phone_match = re.search(r"(?<!\d)(1\d{10})(?!\d)", question)
        if phone_match and any(keyword in question for keyword in ["手机号", "手机", "电话", "联系方式"]):
            phone = phone_match.group(1)
            columns = [
                column
                for column in ["mobile", "contact_mobile"]
                if table.column(column)
            ]
            if columns:
                return "(" + " OR ".join(f"{alias}.{column} = '{phone}'" for column in columns) + ")"

        card_match = re.search(r"(?<![A-Za-z0-9])([0-9Xx]{15,18})(?![A-Za-z0-9])", question)
        if card_match and any(keyword in question for keyword in ["身份证", "证件号", "证件"]):
            card_no = card_match.group(1)
            if table.column("card_no"):
                return f"{alias}.card_no = '{card_no}'"
        return None

    def _group_dimension(
        self,
        question: str,
        primary_table: str,
        candidate_tables: list[str],
    ) -> tuple[str, str] | None:
        if not self._is_group_question(question):
            return None
        group_keywords = self._group_keywords(question)
        search_tables = [primary_table] + [
            table for table in candidate_tables if table != primary_table and self.catalog.get(table)
        ]
        for table_name in search_tables:
            table = self.catalog.require(table_name)
            column = self._match_column(table, group_keywords)
            if column:
                if table_name == primary_table or self._join_path(table_name, primary_table):
                    return table_name, column
        return None

    def _group_keywords(self, question: str) -> list[str]:
        keywords: list[str] = []
        for pattern in [r"按(.+?)(?:统计|分组|排名|数量|$)", r"各(.+?)(?:的|居民|人口|数量|排名|$)"]:
            match = re.search(pattern, question)
            if match:
                keywords.append(match.group(1).strip())
        keyword_aliases = {
            "状态": ["状态", "status"],
            "分类": ["分类", "类别", "category"],
            "渠道": ["渠道", "来源", "channel", "source", "order_from"],
            "商户": ["商户", "merchant", "merchant_name"],
        }
        for label, values in keyword_aliases.items():
            if label in question:
                keywords.extend(values)
        return list(dict.fromkeys(item for item in keywords if item))

    def _match_column(self, table: TableSchema, keywords: list[str]) -> str | None:
        for keyword in keywords:
            lowered = keyword.lower()
            for column in table.columns.values():
                if column.sensitive or not column.searchable:
                    continue
                display = column.display_name or ""
                if lowered == column.name.lower() or keyword in display or display in keyword:
                    return column.name
        return None

    def _build_sql(self, shape: QueryShape) -> str:
        if shape.kind == "group_count" and shape.group_table and shape.group_column:
            return self._build_group_count(shape)
        alias = self._alias(shape.primary_table)
        from_sql = self._from_with_required(shape.primary_table, shape.required_tables)
        filters = self._filters_for_sql(shape)
        if shape.kind == "detail":
            columns = self._detail_columns(shape.primary_table, shape.question)
            return f"SELECT {columns} {from_sql}{filters}"
        count_expr = f"DISTINCT {alias}.id" if shape.required_tables else "*"
        return f"SELECT COUNT({count_expr}) AS total {from_sql}{filters}"

    def _build_group_count(self, shape: QueryShape) -> str:
        assert shape.group_table and shape.group_column
        group_alias = self._alias(shape.group_table)
        primary_alias = self._alias(shape.primary_table)
        from_sql = self._from_with_required(
            shape.group_table,
            [shape.primary_table, *shape.required_tables],
        )
        filters = self._filters_for_sql(shape)
        distinct_expr = f"DISTINCT {primary_alias}.id"
        label_alias = f"{shape.group_column}_value"
        return (
            f"SELECT {group_alias}.{shape.group_column} AS {label_alias}, "
            f"COUNT({distinct_expr}) AS total "
            f"{from_sql}{filters} "
            f"GROUP BY {group_alias}.{shape.group_column} ORDER BY total DESC"
        )

    def _from_with_required(self, start_table: str, required_tables: tuple[str, ...] | list[str]) -> str:
        sql = f"FROM {start_table} {self._alias(start_table)}"
        joined = {start_table.lower()}
        for target in required_tables:
            if target.lower() in joined:
                continue
            path = self._join_path_from_any(joined, target)
            if not path:
                raise RejectedQuery(
                    f"schema 中没有可用 join 路径: {start_table} -> {target}",
                    "no_join_path",
                )
            for step in path:
                if step.table.lower() in joined:
                    continue
                sql += f" JOIN {step.table} {self._alias(step.table)} ON {self._alias_join(step.on)}"
                joined.add(step.table.lower())
        return sql

    def _join_path(self, start_table: str, end_table: str) -> list[JoinStep]:
        if start_table == end_table:
            return []
        queue = deque([(start_table, [])])
        visited = {start_table}
        while queue:
            table_name, path = queue.popleft()
            for step in self._neighbors(table_name):
                if step.table in visited:
                    continue
                new_path = [*path, step]
                if step.table == end_table:
                    return new_path
                visited.add(step.table)
                queue.append((step.table, new_path))
        return []

    def _join_path_from_any(self, start_tables: set[str], end_table: str) -> list[JoinStep]:
        shortest: list[JoinStep] | None = None
        for start in start_tables:
            path = self._join_path(start, end_table.lower())
            if path and (shortest is None or len(path) < len(shortest)):
                shortest = path
        return shortest or []

    def _neighbors(self, table_name: str) -> list[JoinStep]:
        neighbors: list[JoinStep] = []
        table = self.catalog.get(table_name)
        if table:
            neighbors.extend(JoinStep(table=join.to_table, on=join.on) for join in table.joins)
        for other in self.catalog.tables:
            for join in other.joins:
                if join.to_table.lower() == table_name.lower():
                    neighbors.append(JoinStep(table=other.name, on=join.on))
        return neighbors

    def _alias_join(self, join_on: str) -> str:
        result = join_on
        for table in sorted(self.catalog.table_names, key=len, reverse=True):
            result = re.sub(rf"\b{re.escape(table)}\.", f"{self._alias(table)}.", result)
        return result

    def _filters_for_sql(self, shape: QueryShape) -> str:
        if not shape.filters:
            return ""
        return " WHERE " + " AND ".join(shape.filters)

    def _detail_columns(self, table_name: str, question: str = "") -> str:
        table = self.catalog.require(table_name)
        alias = self._alias(table_name)
        selected = [
            column.name
            for column in table.columns.values()
            if not column.sensitive and column.searchable
        ][:8]
        for column in self._requested_sensitive_columns(table, question):
            if column not in selected:
                selected.append(column)
        return ", ".join(f"{alias}.{column}" for column in selected) or f"{alias}.id"

    def _requested_sensitive_columns(self, table: TableSchema, question: str) -> list[str]:
        if not self.allow_sensitive_fields:
            return []
        keyword_to_columns = {
            "手机号": ["mobile", "contact_mobile"],
            "手机": ["mobile", "contact_mobile"],
            "电话": ["mobile", "contact_mobile"],
            "联系方式": ["mobile", "contact_mobile"],
            "证件号": ["card_no"],
            "身份证": ["card_no"],
            "证件": ["card_no"],
            "card": ["card_no"],
            "mobile": ["mobile"],
        }
        requested: list[str] = []
        lowered = question.lower()
        for keyword, columns in keyword_to_columns.items():
            if keyword.lower() not in lowered:
                continue
            for column in columns:
                if table.column(column) and column not in requested:
                    requested.append(column)
        return requested

    def _is_group_question(self, question: str) -> bool:
        return any(keyword in question for keyword in ["按", "各", "分组", "排名", "分布"])

    def _is_detail_question(self, question: str) -> bool:
        return any(keyword in question for keyword in ["列表", "明细", "有哪些", "查一下", "查询"])

    def _alias(self, table_name: str) -> str:
        parts = table_name.split("_")
        base = "".join(part[0] for part in parts if part)
        aliases = {
            "payment_order": "po",
            "refund_order": "ro",
            "merchant": "m",
        }
        return aliases.get(table_name, base or "t")

    def _describe_shape(self, shape: QueryShape) -> str:
        if shape.kind == "group_count":
            return (
                f"根据 schema 从 {shape.primary_table} 出发，按 "
                f"{shape.group_table}.{shape.group_column} 动态分组统计。"
            )
        if shape.kind == "detail":
            return f"根据 schema 选择 {shape.primary_table} 的非敏感字段返回明细。"
        return f"根据 schema 统计 {shape.primary_table} 数量。"


class OpenAICompatibleSqlGenerator:
    def __init__(self, settings: LlmSettings, fallback: SchemaDrivenSqlGenerator) -> None:
        self.settings = settings
        self.fallback = fallback

    def generate(
        self,
        question: str,
        schema_context: str,
        candidate_tables: list[str],
    ) -> GeneratedSql:
        if not self.settings.configured:
            return self.fallback.generate(question, schema_context, candidate_tables)
        system_prompt = self._system_prompt()
        user_prompt = self._user_prompt(question, schema_context)
        llm_log = self._new_llm_log(system_prompt, user_prompt, candidate_tables)
        started = time.monotonic()
        try:
            if self.settings.transport == "anthropic":
                content = self._generate_with_anthropic(system_prompt, user_prompt, llm_log)
            else:
                content = self._generate_with_openai(system_prompt, user_prompt, llm_log)
            llm_log["elapsedMs"] = int((time.monotonic() - started) * 1000)
            llm_log["status"] = "ok"
            llm_log["response"] = {"content": content}
            parsed = _parse_llm_content(content)
            llm_log["parsed"] = {"sql": str(parsed["sql"]), "plan": str(parsed.get("plan", ""))}
            return GeneratedSql(
                sql=str(parsed["sql"]),
                plan=str(parsed.get("plan", "LLM generated SQL")),
                hit_path="guarded_text2sql",
                interaction_logs=[llm_log],
            )
        except Exception as exc:
            llm_log["elapsedMs"] = int((time.monotonic() - started) * 1000)
            llm_log["status"] = "error"
            llm_log["error"] = str(exc)
            fallback = self.fallback.generate(question, schema_context, candidate_tables)
            fallback_log = {
                "kind": "fallback",
                "status": "ok",
                "generator": "schema_driven",
                "reason": str(exc),
                "sql": fallback.sql,
                "plan": fallback.plan,
            }
            return GeneratedSql(
                sql=fallback.sql,
                plan=f"LLM SQL 生成失败，已回退到 schema-driven: {exc}. {fallback.plan}",
                hit_path="schema_driven_fallback",
                interaction_logs=[llm_log, fallback_log],
            )

    def _system_prompt(self) -> str:
        return (
            "You are a Text-to-SQL engine. Return JSON only: "
            "{\"sql\":\"SELECT ...\", \"plan\":\"...\"}. The SQL must be exactly "
            "one MySQL SELECT statement using only provided schema names."
        )

    def _user_prompt(self, question: str, schema_context: str) -> str:
        return f"{schema_context}\n\nQuestion: {question}"

    def _new_llm_log(
        self,
        system_prompt: str,
        user_prompt: str,
        candidate_tables: list[str],
    ) -> dict[str, Any]:
        return {
            "kind": "llm",
            "status": "pending",
            "transport": self.settings.transport,
            "model": self.settings.model,
            "baseUrl": self.settings.base_url,
            "timeoutSeconds": self.settings.timeout_seconds,
            "candidateTables": candidate_tables,
            "request": {
                "model": self.settings.model,
                "temperature": self.settings.temperature,
                "maxTokens": self.settings.max_tokens,
                "system": system_prompt,
                "user": user_prompt,
            },
        }

    def _generate_with_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        llm_log: dict[str, Any],
    ) -> str:
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.settings.model,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = httpx.post(
            self.settings.base_url,
            headers=headers,
            json=payload,
            timeout=self.settings.timeout_seconds,
        )
        llm_log["statusCode"] = response.status_code
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        content = message.get("content") or ""
        if not content.strip():
            raise ValueError("LLM response missing content")
        return str(content)

    def _generate_with_anthropic(
        self,
        system_prompt: str,
        user_prompt: str,
        llm_log: dict[str, Any],
    ) -> str:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise RuntimeError("anthropic sdk is not installed") from exc
        client = anthropic.Anthropic(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
        )
        response = client.messages.create(
            model=self.settings.model,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
        )
        llm_log["responseMeta"] = {
            "stopReason": getattr(response, "stop_reason", None),
            "model": getattr(response, "model", None),
        }
        usage = getattr(response, "usage", None)
        if usage is not None:
            llm_log["responseMeta"]["usage"] = {
                "inputTokens": getattr(usage, "input_tokens", None),
                "outputTokens": getattr(usage, "output_tokens", None),
            }
        text_parts: list[str] = []
        for block in list(getattr(response, "content", []) or []):
            if getattr(block, "type", "") != "text":
                continue
            text = getattr(block, "text", "")
            if isinstance(text, str) and text.strip():
                text_parts.append(text)
        content = "\n".join(text_parts).strip()
        if not content:
            raise ValueError("Anthropic response missing text content")
        return content


def _parse_llm_content(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json|sql)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    if stripped.lower().startswith("select"):
        return {"sql": stripped, "plan": "raw SQL from LLM"}
    parsed = json.loads(_extract_json_object(stripped))
    if not parsed.get("sql"):
        raise ValueError("LLM response missing sql")
    return parsed


def _extract_json_object(content: str) -> str:
    if content.startswith("{") and content.endswith("}"):
        return content
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response missing JSON object")
    return content[start : end + 1]
