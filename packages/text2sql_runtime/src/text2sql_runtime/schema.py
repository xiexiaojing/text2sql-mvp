from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import load_yaml
from .models import ColumnSchema, JoinPath, TableSchema


class SchemaCatalog:
    def __init__(self, tables: dict[str, TableSchema]) -> None:
        self._tables = tables
        self._by_object = {table.object_name.lower(): table for table in tables.values()}

    @classmethod
    def from_whitelist(cls, path: Path) -> "SchemaCatalog":
        raw = load_yaml(path)
        tables: dict[str, TableSchema] = {}
        for item in raw.get("tables", []):
            if not isinstance(item, dict):
                continue
            name = str(item["name"])
            raw_columns = item.get("columns", [])
            for column in raw_columns:
                if not isinstance(column.get("name"), str):
                    raise ValueError(
                        f"Column name must be a string in table {name}: {column.get('name')!r}. "
                        "Quote YAML boolean-like names such as no/yes/on/off."
                    )
            columns = {
                str(column["name"]): ColumnSchema(
                    name=str(column["name"]),
                    display_name=column.get("display_name"),
                    data_type=column.get("type"),
                    sensitive=bool(column.get("sensitive", False)),
                    searchable=bool(column.get("searchable", True)),
                )
                for column in raw_columns
            }
            joins = []
            for join in item.get("joins", []):
                if "to_table" not in join:
                    continue
                on_value = join.get("on", join.get(True))
                if on_value is None:
                    continue
                joins.append(JoinPath(to_table=str(join["to_table"]), on=str(on_value)))
            tables[name.lower()] = TableSchema(
                name=name,
                object_name=str(item.get("object", name)),
                display_name=str(item.get("display_name", name)),
                domain_column=item.get("domain_column"),
                columns=columns,
                source_class=item.get("source_class"),
                row_count_estimate=int(item.get("row_count_estimate", 0) or 0),
                indexes=list(item.get("indexes", [])),
                joins=joins,
            )
        return cls(tables)

    @property
    def tables(self) -> list[TableSchema]:
        return list(self._tables.values())

    @property
    def table_names(self) -> set[str]:
        return set(self._tables)

    def get(self, name: str) -> TableSchema | None:
        return self._tables.get(name.lower())

    def require(self, name: str) -> TableSchema:
        table = self.get(name)
        if table is None:
            raise KeyError(name)
        return table

    def by_object(self, object_name: str) -> TableSchema | None:
        return self._by_object.get(object_name.lower())

    def allowed_join_pairs(self) -> set[frozenset[str]]:
        pairs: set[frozenset[str]] = set()
        for table in self.tables:
            for join in table.joins:
                pairs.add(frozenset({table.name.lower(), join.to_table.lower()}))
        return pairs

    def column_names_for_tables(self, table_names: list[str]) -> set[str]:
        names: set[str] = set()
        for table_name in table_names:
            table = self.get(table_name)
            if table:
                names.update(column.lower() for column in table.columns)
        return names

    def summary(self) -> dict[str, Any]:
        return {
            "table_count": len(self._tables),
            "tables": [
                {
                    "name": table.name,
                    "object": table.object_name,
                    "display_name": table.display_name,
                    "domain_column": table.domain_column,
                    "columns": [
                        {
                            "name": column.name,
                            "display_name": column.display_name,
                            "sensitive": column.sensitive,
                            "searchable": column.searchable,
                        }
                        for column in table.columns.values()
                    ],
                    "joins": [{"to_table": join.to_table, "on": join.on} for join in table.joins],
                }
                for table in sorted(self.tables, key=lambda item: item.name)
            ],
        }
