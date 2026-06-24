from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_yaml

FIELD_QUESTION_ALIASES: dict[str, tuple[str, ...]] = {
    "card_no": ("身份证", "证件号", "证件", "card_no"),
    "payer_mobile": ("付款手机", "付款手机号", "payer_mobile", "手机号", "手机"),
    "mobile": ("手机号", "手机", "mobile", "电话"),
    "contact_mobile": ("联系手机", "contact_mobile", "联系电话"),
    "born_at": ("出生", "年龄", "born_at", "生日"),
    "residence_status": ("居住状况", "居住状态", "residence_status"),
    "household_status": ("户籍状况", "户籍状态", "household_status"),
    "permanent": ("常住", "permanent"),
}


@dataclass(frozen=True)
class FieldEnrichment:
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableEnrichment:
    source_class: str | None
    notes: tuple[str, ...]
    fields: dict[str, FieldEnrichment]


@dataclass(frozen=True)
class SemanticEnrichmentIndex:
    global_notes: tuple[str, ...]
    tables: dict[str, TableEnrichment]

    @classmethod
    def from_config(cls, path: Path) -> SemanticEnrichmentIndex:
        if not path.exists():
            return cls(global_notes=(), tables={})
        raw = load_yaml(path)
        global_notes = _string_list(raw.get("global_notes"))
        tables: dict[str, TableEnrichment] = {}
        raw_tables = raw.get("tables")
        if isinstance(raw_tables, dict):
            for table_name, item in raw_tables.items():
                if not isinstance(item, dict):
                    continue
                fields: dict[str, FieldEnrichment] = {}
                raw_fields = item.get("fields")
                if isinstance(raw_fields, dict):
                    for field_name, field_item in raw_fields.items():
                        if not isinstance(field_item, dict):
                            continue
                        fields[str(field_name).lower()] = FieldEnrichment(
                            notes=_string_list(field_item.get("notes"))
                        )
                tables[str(table_name).lower()] = TableEnrichment(
                    source_class=_optional_string(item.get("source_class")),
                    notes=_string_list(item.get("notes")),
                    fields=fields,
                )
        return cls(global_notes=global_notes, tables=tables)

    @property
    def enabled(self) -> bool:
        return bool(self.global_notes or self.tables)

    def context_lines(
        self,
        question: str,
        candidate_tables: list[str],
        *,
        max_lines: int = 10,
    ) -> list[str]:
        if not self.enabled or max_lines <= 0:
            return []
        scored: list[tuple[int, str]] = []
        normalized_question = _normalize(question)
        candidate_set = {table.lower() for table in candidate_tables}

        for index, note in enumerate(self.global_notes):
            scored.append((100 - index, note))

        for table_name, enrichment in self.tables.items():
            table_score = 0
            if table_name in candidate_set:
                table_score += 20
            if table_name.replace("_", " ") in normalized_question:
                table_score += 8
            if table_name in normalized_question:
                table_score += 8
            for note in enrichment.notes:
                if table_score > 0:
                    scored.append((table_score, f"{table_name}: {note}"))
            for field_name, field_enrichment in enrichment.fields.items():
                field_score = table_score
                if field_name in normalized_question:
                    field_score += 10
                for alias in FIELD_QUESTION_ALIASES.get(field_name, ()):
                    if alias.lower() in normalized_question:
                        field_score += 12
                        break
                for note in field_enrichment.notes:
                    if field_score > 0:
                        scored.append((field_score, f"{table_name}.{field_name}: {note}"))

        if not scored:
            return []

        selected: list[str] = []
        seen: set[str] = set()
        for _, note in sorted(scored, key=lambda item: (-item[0], item[1])):
            if note in seen:
                continue
            seen.add(note)
            selected.append(note)
            if len(selected) >= max_lines:
                break
        return selected

    def field_notes(self, table_name: str, column_name: str) -> tuple[str, ...]:
        enrichment = self.tables.get(table_name.lower())
        if enrichment is None:
            return ()
        field = enrichment.fields.get(column_name.lower())
        if field is None:
            return ()
        return field.notes


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for item in value:
        text = _optional_string(item)
        if text:
            items.append(text)
    return tuple(items)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
