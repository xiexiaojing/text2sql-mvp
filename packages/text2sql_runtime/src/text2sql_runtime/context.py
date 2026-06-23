from __future__ import annotations

from typing import Any

from .conversation import conversation_context_lines
from .memory import MemoryRecord, format_memory_context_lines
from .schema import SchemaCatalog
from .semantics import SemanticIndex, epoch_ms_for_age_at_least


class SchemaContextBuilder:
    def __init__(
        self,
        catalog: SchemaCatalog,
        semantics: SemanticIndex,
        allow_sensitive_fields: bool = False,
    ) -> None:
        self.catalog = catalog
        self.semantics = semantics
        self.allow_sensitive_fields = allow_sensitive_fields

    def build(
        self,
        question: str,
        candidate_tables: list[str],
        history: list[dict[str, Any]] | None = None,
        memories: list[MemoryRecord] | None = None,
    ) -> str:
        concepts = self.semantics.detect(question)
        lines = [
            "You generate one safe MySQL SELECT statement for a read-only analytics query.",
            "Return JSON only with keys: sql, plan.",
            "Use physical MySQL table and column names exactly as listed.",
            "Use only the provided tables, columns, functions, and join paths.",
            self._sensitive_field_instruction(),
            "The backend injects tenant/domain filters for scoped tables; do not invent permissions.",
            "If the question mentions refunds, prefer refund_order.",
            "If the question mentions merchants with rankings, join merchant and payment_order.",
            "",
            "Semantic concepts:",
        ]
        if concepts:
            for concept in concepts:
                lines.append(f"- {concept.display_name}: {concept.rule}")
                constraint = self._semantic_constraint_hint(concept.rule)
                if constraint:
                    lines.append(f"  SQL hint: {constraint}")
        else:
            lines.append("- None detected")
        lines.append("")
        if candidate_tables:
            lines.append("Router candidate tables: " + ", ".join(sorted(set(candidate_tables))))
            lines.append("")
        conversation_lines = conversation_context_lines(history)
        if conversation_lines:
            lines.extend(conversation_lines)
            lines.append("")
        memory_lines = format_memory_context_lines(memories or [])
        if memory_lines:
            lines.extend(memory_lines)
            lines.append("")
        sensitive_hint = self._sensitive_filter_hint(question)
        if sensitive_hint:
            lines.append(sensitive_hint)
            lines.append("")
        lines.extend(
            [
                "Business table guide:",
                "- payment_order: payment transactions, channels, amounts, status",
                "- refund_order: refund records linked to payment_order",
                "- merchant: merchant master data",
                "",
            ]
        )
        lines.append("Detailed schema for most relevant tables:")
        context_tables = self._context_tables(question, candidate_tables)
        detailed_tables = set(context_tables)
        for table_name in context_tables:
            if table_name.lower() not in detailed_tables:
                continue
            table = self.catalog.get(table_name)
            if not table:
                continue
            columns = ", ".join(
                column.name
                for column in table.columns.values()
                if self.allow_sensitive_fields or (not column.sensitive and column.searchable)
            )
            joins = "; ".join(f"{table.name} -> {join.to_table} ON {join.on}" for join in table.joins)
            lines.append(f"- {table.name} ({table.display_name}): {columns}")
            if joins:
                lines.append(f"  joins: {joins}")
        return "\n".join(lines)

    def _sensitive_field_instruction(self) -> str:
        if self.allow_sensitive_fields:
            return (
                "Never use SELECT *; choose explicit columns. Sensitive columns such as "
                "mobile, contact_mobile, card_no, caller_phone, caller_name may be used when requested."
            )
        return (
            "Never use SELECT *; choose explicit non-sensitive columns for detail queries. "
            "Do not use sensitive columns such as mobile, card_no, caller_phone, caller_name."
        )

    def _semantic_constraint_hint(self, rule: dict) -> str | None:
        if rule.get("type") != "age_at_least":
            return None
        table_name = str(rule.get("table", "payment_order"))
        column_name = str(rule.get("column", "born_at"))
        if not self.catalog.get(table_name):
            return None
        if not self.catalog.require(table_name).column(column_name):
            return None
        age = int(rule["age"])
        cutoff = epoch_ms_for_age_at_least(age)
        return (
            f"{table_name}.{column_name} IS NOT NULL AND {table_name}.{column_name} <> 0 "
            f"AND {table_name}.{column_name} <= {cutoff} for age >= {age}; "
            "epoch-ms values may be negative for pre-1970 dates; do not use DATE_SUB, CURDATE, YEAR, or date arithmetic."
        )

    def _sensitive_filter_hint(self, question: str) -> str | None:
        if not self.allow_sensitive_fields:
            return None
        if "手机号" in question or "手机" in question or "电话" in question:
            return (
                "Sensitive lookup hint: if a phone number is present, filter merchant or payment_order "
                "by the relevant contact/mobile column; do not answer with an unfiltered total."
            )
        if "身份证" in question or "证件号" in question or "证件" in question:
            return (
                "Sensitive lookup hint: if a certificate number is present, filter by the matching "
                "card/certificate column; do not answer with an unfiltered total."
            )
        return None

    def _context_tables(self, question: str, candidate_tables: list[str]) -> list[str]:
        scored: list[tuple[int, str]] = []
        candidates = {table.lower() for table in candidate_tables}
        selected: list[str] = []

        def add_table(table_name: str) -> None:
            table = self.catalog.get(table_name)
            if table and table.name not in selected:
                selected.append(table.name)

        for table_name in candidate_tables:
            add_table(table_name)
        for table_name in list(selected):
            table = self.catalog.get(table_name)
            if not table:
                continue
            for join in table.joins:
                add_table(join.to_table)

        for table in self.catalog.tables:
            score = 0
            if table.name.lower() in candidates:
                score += 20
            if table.display_name and table.display_name in question:
                score += 10
            if table.object_name.lower() in question.lower():
                score += 6
            for column in table.columns.values():
                if column.display_name and column.display_name in question:
                    score += 2
                if column.name.lower() in question.lower():
                    score += 1
            scored.append((score, table.name))

        for score, table_name in sorted(scored, key=lambda item: (-item[0], item[1])):
            if score <= 0:
                continue
            add_table(table_name)
        if not selected:
            add_table("payment_order")
        return selected[:6]
