from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_yaml
from .schema import SchemaCatalog, TableSchema
from .semantic_enrichment import SemanticEnrichmentIndex
from .models import ColumnSchema

TECHNICAL_FIELD_RE = re.compile(
    r"\b([a-z][a-z0-9_]*)\s*\.\s*([a-z][a-z0-9_]*)\b",
    re.IGNORECASE,
)
QUOTED_FIELD_RE = re.compile(r"[‘'“\"]([\u4e00-\u9fa5A-Za-z0-9_]+)[’'”\"]")

DISPLAY_ALIASES = {
    "居住状况": "居住状态",
    "户口状况": "户籍状态",
    "手机号": "手机号",
}


@dataclass(frozen=True)
class ResolvedField:
    query_label: str
    table_name: str
    table_display_name: str
    object_name: str
    column_name: str
    column_display_name: str | None
    source_class: str | None
    ontology_display_name: str | None
    ontology_notes: str | None
    enrichment_notes: tuple[str, ...]
    allowed_values: tuple[str, ...]


def resolve_field(
    question: str,
    slots: dict[str, Any],
    catalog: SchemaCatalog,
    project_root: Path,
    enrichment: SemanticEnrichmentIndex | None = None,
) -> ResolvedField | None:
    technical = _extract_technical_field(question, slots)
    if technical is not None:
        table_name, column_name = technical
        table = catalog.get(table_name)
        if table is None:
            return None
        column = table.columns.get(column_name.lower()) or table.columns.get(column_name)
        if column is None:
            return None
        label = f"{table.name}.{column.name}"
        return _build_resolved(label, table, column, project_root, enrichment)

    label = _string(slots.get("field_name")) or _extract_quoted_field(question)
    if not label:
        return None
    match = _find_by_label(catalog, label)
    if match is None:
        return None
    table, column = match
    return _build_resolved(label, table, column, project_root, enrichment)


def explain_field(resolved: ResolvedField) -> tuple[str, dict[str, Any]]:
    display = resolved.column_display_name or resolved.ontology_display_name or resolved.column_name
    lines = [
        f"「{resolved.query_label}」对应 {resolved.table_display_name}（{resolved.table_name}）"
        f"字段 {resolved.column_name}（展示名：{display}）。"
    ]
    if resolved.ontology_notes:
        lines.append(resolved.ontology_notes)
    elif resolved.enrichment_notes:
        lines.append(" ".join(resolved.enrichment_notes))
    else:
        lines.append(_default_meaning(resolved))
    if resolved.enrichment_notes and resolved.ontology_notes:
        lines.append(" ".join(resolved.enrichment_notes))
    if resolved.allowed_values:
        values = "、".join(resolved.allowed_values)
        lines.append(f"常见取值：{values}。")
    if resolved.source_class:
        lines.append(f"数据来源：{resolved.source_class}#{_camel_case(resolved.column_name)}。")
    answer = "".join(lines)
    table = {
        "columns": [
            "query_label",
            "table_name",
            "column_name",
            "column_display_name",
            "allowed_values",
        ],
        "rows": [
            {
                "query_label": resolved.query_label,
                "table_name": resolved.table_name,
                "column_name": resolved.column_name,
                "column_display_name": display,
                "allowed_values": "、".join(resolved.allowed_values) if resolved.allowed_values else "",
            }
        ],
        "row_count": 1,
        "mode": "metadata",
    }
    return answer, table


def _build_resolved(
    query_label: str,
    table: TableSchema,
    column: ColumnSchema,
    project_root: Path,
    enrichment: SemanticEnrichmentIndex | None = None,
) -> ResolvedField:
    ontology = _load_ontology_property(table.object_name, column.name, project_root)
    allowed_values = _load_allowed_values(ontology.get("value_type"), project_root)
    enrichment_notes = ()
    if enrichment is not None:
        enrichment_notes = enrichment.field_notes(table.name, column.name)
    return ResolvedField(
        query_label=query_label,
        table_name=table.name,
        table_display_name=table.display_name,
        object_name=table.object_name,
        column_name=column.name,
        column_display_name=column.display_name,
        source_class=table.source_class,
        ontology_display_name=ontology.get("display_name"),
        ontology_notes=ontology.get("notes"),
        enrichment_notes=enrichment_notes,
        allowed_values=allowed_values,
    )


def _find_by_label(catalog: SchemaCatalog, label: str) -> tuple[TableSchema, ColumnSchema] | None:
    normalized = DISPLAY_ALIASES.get(label.strip(), label.strip())
    matches: list[tuple[TableSchema, ColumnSchema]] = []
    for table in catalog.tables:
        for column in table.columns.values():
            display = (column.display_name or "").strip()
            if display and (display == normalized or display == label.strip()):
                matches.append((table, column))
                continue
            if column.name.lower() == _to_snake(normalized).lower():
                matches.append((table, column))
    if not matches:
        return None
    resident_matches = [item for item in matches if item[0].name.lower() == "resident"]
    if len(resident_matches) == 1:
        return resident_matches[0]
    if len(matches) == 1:
        return matches[0]
    return resident_matches[0] if resident_matches else matches[0]


def _extract_technical_field(question: str, slots: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("table_name", "column_name"):
        if key not in slots:
            continue
    table_name = _string(slots.get("table_name"))
    column_name = _string(slots.get("column_name"))
    if table_name and column_name:
        return table_name.lower(), column_name.lower()
    match = TECHNICAL_FIELD_RE.search(question)
    if match is None:
        return None
    return match.group(1).lower(), match.group(2).lower()


def _extract_quoted_field(question: str) -> str | None:
    match = QUOTED_FIELD_RE.search(question)
    return match.group(1) if match else None


def _load_ontology_property(object_name: str, column_name: str, project_root: Path) -> dict[str, str | None]:
    root = _ontology_root(project_root)
    if root is None:
        return {}
    objects_dir = root / "objects"
    if not objects_dir.exists():
        return {}
    snake = column_name.lower()
    camel = _camel_case(column_name)
    for path in sorted(objects_dir.glob("*.yaml")):
        raw = load_yaml(path)
        if str(raw.get("metadata", {}).get("canonicalApiName", "")).lower() != object_name.lower():
            continue
        for item in raw.get("properties", []):
            if not isinstance(item, dict):
                continue
            source_field = str(item.get("sourceField") or item.get("sourceName") or "").lower()
            canonical = str(item.get("canonicalApiName") or "").lower()
            if source_field not in {snake, camel.lower()} and canonical not in {snake, camel.lower()}:
                continue
            notes = item.get("notes")
            return {
                "display_name": _string(item.get("displayName")),
                "notes": _string(notes) if notes else None,
                "value_type": _string(item.get("valueType")),
            }
    return {}


def _load_allowed_values(value_type: str | None, project_root: Path) -> tuple[str, ...]:
    if not value_type:
        return ()
    root = _ontology_root(project_root)
    if root is None:
        return ()
    raw = load_yaml(root / "runtime" / "value_types.yaml")
    for item in raw.get("valueTypes", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("canonicalApiName") or "") != value_type:
            continue
        labels = []
        for value in item.get("allowedValues", []):
            if not isinstance(value, dict):
                continue
            label = _string(value.get("label")) or _string(value.get("sourceValue"))
            if label:
                labels.append(label)
        return tuple(labels)
    return ()


def _default_meaning(resolved: ResolvedField) -> str:
    if resolved.column_name == "channel":
        return "Payment channel such as wechat, alipay, or unionpay."
    if resolved.column_name == "status":
        return "Business status code for the current record."
    if resolved.column_name == "payer_mobile":
        return "Payer mobile number; treated as a sensitive field at runtime."
    return "Field from the whitelisted schema used by the Text2SQL runtime."


def _ontology_root(project_root: Path) -> Path | None:
    # Optional external ontology bundle; not required for the open-source demo.
    return None


def _camel_case(value: str) -> str:
    parts = value.strip("_").split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:] if part)


def _to_snake(value: str) -> str:
    normalized = []
    for index, char in enumerate(value):
        if char.isupper() and index > 0:
            normalized.append("_")
        normalized.append(char.lower())
    return "".join(normalized)


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
