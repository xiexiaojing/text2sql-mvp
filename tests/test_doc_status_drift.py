from __future__ import annotations

import re
from pathlib import Path

import pytest

from text2sql_runtime.service import _EXECUTABLE_STATUSES

ARCHITECTURE_MD = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"
STATUS_TABLE_HEADER = re.compile(r"^\|\s*分支\s*\|\s*配置\s*status\s*\|.*$", re.MULTILINE)
BACKTICKED_STATUS_RE = re.compile(r"`([a-z_][a-z0-9_]*)`")

# Config-level statuses that an intent in business_semantics.yaml may declare.
# Anything else (needs_clarification / unsupported) is only produced by the
# runtime routing in business_semantics._build_plan.
DECLARABLE_INTENT_STATUSES = frozenset(_EXECUTABLE_STATUSES | {"needs_mapping"})


def _parse_status_table_values(markdown: str) -> frozenset[str]:
    lines = markdown.splitlines()
    header_index: int | None = None
    for index, line in enumerate(lines):
        if STATUS_TABLE_HEADER.match(line):
            header_index = index
            break
    assert header_index is not None, "架构文档缺少「配置 status」表格表头"

    values: set[str] = set()
    started = False
    for line in lines[header_index + 1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if started:
                break
            continue
        if re.match(r"^\|[-\s|]+\|$", stripped):
            started = True
            continue
        started = True
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        values.update(BACKTICKED_STATUS_RE.findall(cells[1]))
    return frozenset(values)


def test_status_table_matches_code_enum():
    """架构文档 status 表格必须列全代码中允许在 yaml 声明的 status。

    改坏文档表格（增删条目、拼错取值）时这个测试会红。改动代码中的
    枚举后，也要求同步文档。
    """
    doc_statuses = _parse_status_table_values(ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert doc_statuses, "从架构文档 status 表格里没解析出任何取值"
    assert doc_statuses == DECLARABLE_INTENT_STATUSES, (
        f"文档 status 表格与代码枚举漂移：\n"
        f"  文档: {sorted(doc_statuses)}\n"
        f"  代码: {sorted(DECLARABLE_INTENT_STATUSES)}"
    )


def test_status_parser_rejects_broken_table(tmp_path: Path):
    """把 status 表格改坏后，解析结果不应与代码枚举一致——用来自证测试有效性。"""
    broken = ARCHITECTURE_MD.read_text(encoding="utf-8").replace(
        "| 需映射 | `needs_mapping` | 口径未配置，拒答并返回原因 |",
        "",  # 删掉 needs_mapping 一行
    )
    doc_statuses = _parse_status_table_values(broken)
    assert doc_statuses != DECLARABLE_INTENT_STATUSES

    with pytest.raises(AssertionError):
        assert doc_statuses == DECLARABLE_INTENT_STATUSES
