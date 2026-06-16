from __future__ import annotations

import re
from typing import Any

_HIDDEN_ID_COLUMN_RE = re.compile(r"^(id|.*_id)$", re.IGNORECASE)


def is_hidden_id_column(column: str) -> bool:
    """Hide primary/foreign key columns from user-facing result tables."""
    normalized = str(column or "").strip()
    if not normalized:
        return False
    return bool(_HIDDEN_ID_COLUMN_RE.match(normalized))


def filter_public_table(table: dict[str, Any] | None) -> dict[str, Any] | None:
    if not table:
        return table
    columns = list(table.get("columns") or [])
    if not columns:
        return table
    column_labels = list(table.get("column_labels") or columns)
    if len(column_labels) != len(columns):
        column_labels = list(columns)
    rows = list(table.get("rows") or [])

    keep_indices = [index for index, column in enumerate(columns) if not is_hidden_id_column(column)]
    if len(keep_indices) == len(columns):
        return table

    public_columns = [columns[index] for index in keep_indices]
    public_labels = [column_labels[index] for index in keep_indices]
    public_rows = [{column: row.get(column) for column in public_columns} for row in rows]
    return {
        **table,
        "columns": public_columns,
        "column_labels": public_labels,
        "rows": public_rows,
        "row_count": len(public_rows),
    }
