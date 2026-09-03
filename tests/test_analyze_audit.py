from __future__ import annotations

import importlib.util
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "analyze_audit.py"


@pytest.fixture(scope="module")
def analyze_audit():
    spec = importlib.util.spec_from_file_location("analyze_audit", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["analyze_audit"] = module
    spec.loader.exec_module(module)
    return module


def _init_audit(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE query_audit (
                query_id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL,
                user_id TEXT,
                domain_id TEXT,
                question TEXT NOT NULL,
                status TEXT NOT NULL,
                hit_path TEXT,
                sql TEXT,
                rejection_reason TEXT,
                elapsed_ms INTEGER NOT NULL,
                scanned_rows INTEGER NOT NULL,
                explain_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                warnings_json TEXT NOT NULL,
                interaction_logs_json TEXT NOT NULL DEFAULT '[]'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert(
    db_path: Path,
    *,
    question: str,
    status: str,
    hit_path: str | None,
    elapsed_ms: int,
    intent: str | None = None,
    template_id: str | None = None,
    output_type: str | None = None,
    rejection_reason: str | None = None,
    created_at: int = 1_700_000_000_000,
    dup: int = 1,
) -> None:
    import json

    conn = sqlite3.connect(db_path)
    try:
        for i in range(dup):
            logs = []
            if intent or template_id:
                cand = []
                if intent:
                    cand.append({"intent": intent, "output_type": output_type})
                logs.append(
                    {
                        "kind": "semantic_planner",
                        "templateId": template_id,
                        "intent": intent,
                        "candidateIntents": cand,
                    }
                )
            conn.execute(
                "INSERT INTO query_audit (query_id, created_at, user_id, domain_id, question, status,"
                " hit_path, sql, rejection_reason, elapsed_ms, scanned_rows, explain_json, result_json,"
                " warnings_json, interaction_logs_json)"
                " VALUES (?, ?, 'eval', 'demo', ?, ?, ?, NULL, ?, ?, 0, '[]', '{}', '[]', ?)",
                (
                    f"q-{uuid.uuid4().hex}-{i}",
                    created_at + i,
                    question,
                    status,
                    hit_path,
                    rejection_reason,
                    elapsed_ms,
                    json.dumps(logs, ensure_ascii=False),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _write_cases(path: Path, cases: list[dict]) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump({"version": 1, "cases": cases}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_analyze_audit_end_to_end(analyze_audit, tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    _init_audit(db_path)
    _insert(
        db_path,
        question="支付订单总数是多少",
        status="planned",
        hit_path="semantic_template",
        elapsed_ms=1,
        intent="payment_order_count",
        template_id="payment_order_count",
        output_type="scalar_count",
        dup=3,
    )
    _insert(
        db_path,
        question="各支付渠道交易金额分布",
        status="planned",
        hit_path="semantic_template",
        elapsed_ms=2,
        intent="payment_channel_amount_distribution",
        template_id="payment_channel_amount_distribution",
        output_type="grouped_metric",
        dup=2,
    )
    _insert(
        db_path,
        question="支付订单平均金额",
        status="planned",
        hit_path="dynamic_entity_query",
        elapsed_ms=0,
        intent="payment_amount_average",
        template_id="dynamic_entity_query",
    )
    _insert(
        db_path,
        question="请统计火星基地飞船泊位能耗",
        status="rejected",
        hit_path="rejected",
        elapsed_ms=0,
        intent="unconfigured_demo",
        rejection_reason="Demo intent kept to show rejection when semantics are not mapped.",
        dup=2,
    )
    _insert(
        db_path,
        question="近7天每日退款笔数趋势",
        status="rejected",
        hit_path="rejected",
        elapsed_ms=0,
        rejection_reason="函数不在白名单中: DATE_SUB",
    )

    cases_path = tmp_path / "cases.yaml"
    _write_cases(
        cases_path,
        [
            {
                "id": "count-case",
                "question": "支付订单总数是多少",
                "domainId": "demo",
                "golden_sql": "SELECT 1",
                "expected": {"tables": ["payment_order"], "features": ["count"]},
            },
            {
                "id": "chart-case",
                "question": "各支付渠道交易金额分布",
                "domainId": "demo",
                "golden_sql": "SELECT 1",
                "expected": {"tables": ["payment_order"]},
            },
            {
                "id": "reject-case",
                "question": "请统计火星基地飞船泊位能耗",
                "domainId": "demo",
                "expected": {"rejected": True},
            },
        ],
    )
    semantics_path = tmp_path / "business_semantics.yaml"
    import yaml as _yaml

    semantics_path.write_text(
        _yaml.safe_dump(
            {
                "intents": [
                    {"id": "payment_order_count", "status": "executable", "examples": ["支付订单总数是多少"]},
                    {
                        "id": "payment_channel_amount_distribution",
                        "status": "executable",
                        "examples": ["各支付渠道交易金额分布"],
                    },
                    {
                        "id": "payment_amount_average",
                        "status": "executable",
                        "examples": ["支付订单平均金额"],
                    },
                    {
                        "id": "payment_phone_lookup",
                        "status": "executable",
                        "examples": ["按手机号查询支付订单"],
                    },
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    out_md = tmp_path / "report.md"
    assets_dir = tmp_path / "assets"
    exit_code = analyze_audit.main(
        [
            "--db",
            str(db_path),
            "--cases",
            str(cases_path),
            "--out",
            str(out_md),
            "--assets",
            str(assets_dir),
            "--semantics",
            str(semantics_path),
        ]
    )
    assert exit_code == 0

    markdown = out_md.read_text(encoding="utf-8")
    assert "记录总数: 9" in markdown
    assert "`semantic_template` | 5" in markdown
    assert "`rejected` | 3" in markdown
    assert "`dynamic_entity_query` | 1" in markdown
    assert "min: 0 ms" in markdown
    assert "max: 2 ms" in markdown
    # rejection breakdown
    assert "Demo intent kept" in markdown
    assert "函数不在白名单中: DATE_SUB" in markdown
    # chart hits: only payment_channel_amount_distribution intent qualifies (2 records)
    assert "命中图表的 2 条" in markdown
    # coverage: count-case ✓, chart-case ✓, reject-case ✓
    assert "`count-case`" in markdown
    assert "`chart-case`" in markdown
    # gap should include the historical whitelist reason AND the untested configured intent
    assert "函数不在白名单中: DATE_SUB" in markdown
    assert "payment_phone_lookup" in markdown
    # svg assets exist
    for name in ("hit-path.svg", "rejection.svg", "elapsed.svg", "chart-intents.svg"):
        svg_path = assets_dir / name
        assert svg_path.exists(), name
        body = svg_path.read_text(encoding="utf-8")
        assert body.startswith("<svg")
        assert "prefers-color-scheme: dark" in body


def test_analyze_audit_output_is_deterministic(analyze_audit, tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    _init_audit(db_path)
    _insert(
        db_path,
        question="支付订单总数是多少",
        status="planned",
        hit_path="semantic_template",
        elapsed_ms=1,
        intent="payment_order_count",
        template_id="payment_order_count",
        output_type="scalar_count",
    )
    _insert(
        db_path,
        question="请统计火星基地飞船泊位能耗",
        status="rejected",
        hit_path="rejected",
        elapsed_ms=0,
        intent="unconfigured_demo",
        rejection_reason="Demo intent kept to show rejection when semantics are not mapped.",
    )

    cases_path = tmp_path / "cases.yaml"
    _write_cases(
        cases_path,
        [
            {
                "id": "count-case",
                "question": "支付订单总数是多少",
                "domainId": "demo",
                "golden_sql": "SELECT 1",
                "expected": {"tables": ["payment_order"]},
            },
            {
                "id": "reject-case",
                "question": "请统计火星基地飞船泊位能耗",
                "domainId": "demo",
                "expected": {"rejected": True},
            },
        ],
    )

    out_a = tmp_path / "a" / "report.md"
    out_b = tmp_path / "b" / "report.md"
    for out in (out_a, out_b):
        analyze_audit.main(
            [
                "--db",
                str(db_path),
                "--cases",
                str(cases_path),
                "--out",
                str(out),
                "--assets",
                str(out.parent / "assets"),
                "--semantics",
                str(tmp_path / "missing_semantics.yaml"),
            ]
        )

    assert out_a.read_text(encoding="utf-8") == out_b.read_text(encoding="utf-8")
    for name in ("hit-path.svg", "rejection.svg", "elapsed.svg", "chart-intents.svg"):
        assert (out_a.parent / "assets" / name).read_text(encoding="utf-8") == (
            out_b.parent / "assets" / name
        ).read_text(encoding="utf-8")


def test_percentile_helper(analyze_audit):
    assert analyze_audit._percentile([], 50) == 0
    assert analyze_audit._percentile([5], 90) == 5
    assert analyze_audit._percentile([1, 2, 3, 4, 5], 50) == 3
    assert analyze_audit._percentile([1, 2, 3, 4, 5], 90) == 5
    assert analyze_audit._percentile([1, 2, 3, 4, 5], 100) == 5


def test_read_records_uses_readonly_uri(analyze_audit, tmp_path):
    db_path = tmp_path / "audit.sqlite3"
    _init_audit(db_path)
    _insert(
        db_path,
        question="q",
        status="planned",
        hit_path="semantic_template",
        elapsed_ms=0,
        intent="x",
    )
    records = analyze_audit._read_records(db_path)
    assert len(records) == 1
    assert records[0].intent == "x"
    # Second read should not raise; ordering is deterministic
    again = analyze_audit._read_records(db_path)
    assert [r.query_id for r in records] == [r.query_id for r in again]
