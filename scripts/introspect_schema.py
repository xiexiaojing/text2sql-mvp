#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import pymysql
import yaml
from pymysql.cursors import DictCursor

EXCLUDED_SUFFIXES = ("_standard_history",)
EXCLUDED_KEYWORDS = ("temp", "tmp", "import", "export", "backup", "process_record")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reviewable whitelist candidates from MySQL.")
    parser.add_argument("--database", default=os.getenv("TEXT2SQL_MYSQL_DATABASE"), required=False)
    parser.add_argument("--output", default="configs/whitelist.generated.yaml")
    parser.add_argument("--include-excluded", action="store_true")
    args = parser.parse_args()

    database = args.database
    if not database:
        raise SystemExit("Missing --database or TEXT2SQL_MYSQL_DATABASE")
    connection = pymysql.connect(
        host=os.getenv("TEXT2SQL_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("TEXT2SQL_MYSQL_PORT", "3306")),
        user=os.getenv("TEXT2SQL_MYSQL_USER"),
        password=os.getenv("TEXT2SQL_MYSQL_PASSWORD"),
        database=database,
        cursorclass=DictCursor,
        charset="utf8mb4",
    )
    with connection:
        tables = load_tables(connection, database, args.include_excluded)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        yaml.safe_dump({"version": 1, "tables": tables}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {output} with {len(tables)} tables")


def load_tables(connection, database: str, include_excluded: bool) -> list[dict[str, Any]]:
    with connection.cursor(DictCursor) as cursor:
        cursor.execute(
            """
            SELECT table_name, table_rows
            FROM information_schema.tables
            WHERE table_schema = %s AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (database,),
        )
        table_rows = cursor.fetchall()
    tables = []
    for row in table_rows:
        table_name = row["table_name"]
        if not include_excluded and is_excluded(table_name):
            continue
        columns = load_columns(connection, database, table_name)
        indexes = load_indexes(connection, database, table_name)
        domain_column = next(
            (column["name"] for column in columns if column["name"].lower() == "domainid"),
            None,
        )
        tables.append(
            {
                "name": table_name,
                "display_name": table_name,
                "domain_column": domain_column,
                "row_count_estimate": int(row.get("table_rows") or 0),
                "columns": columns,
                "indexes": indexes,
            }
        )
    return tables


def load_columns(connection, database: str, table_name: str) -> list[dict[str, Any]]:
    with connection.cursor(DictCursor) as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type, column_comment
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (database, table_name),
        )
        rows = cursor.fetchall()
    return [
        {
            "name": row["column_name"],
            "type": row["data_type"],
            "display_name": row.get("column_comment") or row["column_name"],
            "sensitive": row["column_name"].lower() in {"mobile", "cardno", "callerphone", "phone"},
            "searchable": row["column_name"].lower() not in {"mobile", "cardno", "callerphone", "phone"},
        }
        for row in rows
    ]


def load_indexes(connection, database: str, table_name: str) -> list[dict[str, Any]]:
    with connection.cursor(DictCursor) as cursor:
        cursor.execute(
            """
            SELECT index_name, column_name, seq_in_index
            FROM information_schema.statistics
            WHERE table_schema = %s AND table_name = %s
            ORDER BY index_name, seq_in_index
            """,
            (database, table_name),
        )
        rows = cursor.fetchall()
    indexes: dict[str, list[str]] = {}
    for row in rows:
        indexes.setdefault(row["index_name"], []).append(row["column_name"])
    return [{"name": name, "columns": columns} for name, columns in indexes.items()]


def is_excluded(table_name: str) -> bool:
    lowered = table_name.lower()
    return lowered.endswith(EXCLUDED_SUFFIXES) or any(keyword in lowered for keyword in EXCLUDED_KEYWORDS)


if __name__ == "__main__":
    main()
