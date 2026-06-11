from __future__ import annotations

from pathlib import Path

from text2sql_runtime.field_explanation import explain_field, resolve_field
from text2sql_runtime.schema import SchemaCatalog


def test_resolve_field_by_display_alias(project_root: Path):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    resolved = resolve_field(
        "payment_order 表里 status 字段什么意思？",
        {"field_name": "status", "table_name": "payment_order"},
        catalog,
        project_root,
    )
    assert resolved is not None
    assert resolved.table_name == "payment_order"
    assert resolved.column_name == "status"


def test_explain_field_returns_metadata(project_root: Path):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    resolved = resolve_field(
        "payment_order 表里 channel 字段什么意思？",
        {"field_name": "channel", "table_name": "payment_order"},
        catalog,
        project_root,
    )
    assert resolved is not None
    answer, table = explain_field(resolved)
    assert "channel" in answer.lower() or "渠道" in answer
    assert table["mode"] == "metadata"
    assert table["row_count"] == 1
