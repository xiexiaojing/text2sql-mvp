#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

ENCRYPT_CONVERTER_RE = re.compile(r"@Convert\s*\(\s*converter\s*=\s*MixEncryptConverter\.class\s*\)")
TABLE_NAME_RE = re.compile(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"")
CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
FIELD_RE = re.compile(
    r"(?:@Convert\s*\(\s*converter\s*=\s*MixEncryptConverter\.class\s*\)\s*)?"
    r"(?:@[A-Za-z0-9_().,\"\s=]+\s*)*"
    r"private\s+[A-Za-z0-9_<>, ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*[;=]",
    re.MULTILINE,
)
ENTITY_RE = re.compile(r"@Entity\b")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan dingdan JPA entities and merge encrypted-field hints into entity_enrichment.yaml."
    )
    parser.add_argument("source_root", nargs="?", default="../dingdan-server")
    parser.add_argument(
        "--output",
        default="configs/entity_enrichment.yaml",
        help="Target enrichment YAML to update in place.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_root = Path(args.source_root).resolve()
    output_path = Path(args.output).resolve()
    encrypted_fields = scan_encrypted_fields(source_root)
    if not output_path.exists():
        raise SystemExit(f"Missing enrichment config: {output_path}")

    raw = yaml.safe_load(output_path.read_text(encoding="utf-8")) or {}
    tables = raw.setdefault("tables", {})
    if not isinstance(tables, dict):
        raise SystemExit("entity_enrichment.yaml tables must be a mapping")

    changed = 0
    for table_name, fields in sorted(encrypted_fields.items()):
        table_item = tables.setdefault(table_name, {})
        if not isinstance(table_item, dict):
            continue
        field_map = table_item.setdefault("fields", {})
        if not isinstance(field_map, dict):
            continue
        for field_name in sorted(fields):
            column_name = camel_to_snake(field_name)
            field_item = field_map.setdefault(column_name, {})
            if not isinstance(field_item, dict):
                continue
            notes = field_item.setdefault("notes", [])
            if not isinstance(notes, list):
                notes = []
                field_item["notes"] = notes
            hint = "SM4 加密存储（sm4: 前缀），精确查询需对参数加密后与库内密文比较。"
            if hint not in notes:
                notes.append(hint)
                changed += 1

    if args.dry_run:
        print(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
        return

    output_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Updated {output_path}; merged {changed} encrypted-field note(s)")


def scan_encrypted_fields(source_root: Path) -> dict[str, set[str]]:
    encrypted: dict[str, set[str]] = {}
    for path in source_root.rglob("*.java"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not ENTITY_RE.search(text) or not ENCRYPT_CONVERTER_RE.search(text):
            continue
        class_match = CLASS_RE.search(text)
        if not class_match:
            continue
        table_match = TABLE_NAME_RE.search(text)
        table_name = table_match.group(1) if table_match else camel_to_snake(class_match.group(1))
        if table_name.endswith("_standard_history"):
            continue
        for field_name in _encrypted_field_names(text):
            encrypted.setdefault(table_name, set()).add(field_name)
    return encrypted


def _encrypted_field_names(text: str) -> set[str]:
    names: set[str] = set()
    for match in FIELD_RE.finditer(text):
        window_start = max(0, match.start() - 240)
        window = text[window_start : match.start()]
        if "MixEncryptConverter" in window:
            names.add(match.group(1))
    return names


def camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


if __name__ == "__main__":
    main()
