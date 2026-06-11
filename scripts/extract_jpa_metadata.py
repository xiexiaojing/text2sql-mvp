#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ENTITY_RE = re.compile(r"@Entity\b")
TABLE_NAME_RE = re.compile(r"@Table\s*\(\s*name\s*=\s*\"([^\"]+)\"")
CLASS_RE = re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)")
PRIVATE_FIELD_RE = re.compile(r"\bprivate\s+(?:final\s+)?[A-Za-z0-9_<>, ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*[;=]")


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract lightweight JPA metadata from a Java entity tree.")
    parser.add_argument("source_root", nargs="?", default=".")
    parser.add_argument("--output", default="configs/jpa-metadata.generated.json")
    args = parser.parse_args()
    root = Path(args.source_root).resolve()
    metadata = []
    for path in root.rglob("*.java"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not ENTITY_RE.search(text):
            continue
        class_match = CLASS_RE.search(text)
        if not class_match:
            continue
        class_name = class_match.group(1)
        table_match = TABLE_NAME_RE.search(text)
        table_name = table_match.group(1) if table_match else camel_to_snake(class_name)
        if table_name.endswith("_standard_history"):
            continue
        fields = sorted(set(PRIVATE_FIELD_RE.findall(text)))
        metadata.append(
            {
                "class": class_name,
                "table": table_name,
                "path": str(path.relative_to(root)),
                "fields": fields,
            }
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output} with {len(metadata)} entities")


def camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


if __name__ == "__main__":
    main()
