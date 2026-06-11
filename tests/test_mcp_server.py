from __future__ import annotations

from text2sql_mcp import server as mcp_server
from text2sql_runtime.rejection_reasons import UNCONFIGURED_SEMANTIC_REASON


def test_mcp_query_uses_guarded_runtime(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "service", lambda: service)

    result = mcp_server.query(
        question="支付订单总数是多少",
        domain_id="domain-1",
        user_id="mcp-test",
        allow_return_sql=True,
    )

    assert result["status"] == "planned"
    assert "payment_order" in result["generatedSql"]
    assert "tenant_id" in result["generatedSql"]
    assert result["queryId"]


def test_mcp_estimate_and_schema_summary(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "service", lambda: service)

    estimate = mcp_server.estimate("有手机号13800138000的订单吗", "domain-1")
    summary = mcp_server.schema_summary()

    assert estimate["status"] == "accepted"
    assert summary["table_count"] >= 3
    assert summary["execution_mode"] == "dry_run"


def test_mcp_audit_reads_previous_query(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "service", lambda: service)
    created = mcp_server.query("支付订单按状态统计", "domain-1", allow_return_sql=True)

    record = mcp_server.audit(created["queryId"])

    assert record["query_id"] == created["queryId"]
    assert record["status"] == "planned"


def test_mcp_unsupported_questions_reads_audit_collection(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "service", lambda: service)
    service.audit_store.record(
        {
            "query_id": "unsupported-mcp-1",
            "created_at": 1000,
            "user_id": "mcp-test",
            "domain_id": "domain-1",
            "question": "请统计火星基地飞船泊位能耗",
            "status": "rejected",
            "hit_path": "rejected",
            "rejection_reason": UNCONFIGURED_SEMANTIC_REASON,
        }
    )

    result = mcp_server.unsupported_questions()

    assert result["total"] == 1
    assert result["items"][0]["latestQueryId"] == "unsupported-mcp-1"


def test_mcp_query_accepts_history(monkeypatch, service):
    monkeypatch.setattr(mcp_server, "service", lambda: service)

    result = mcp_server.query(
        "那按状态呢",
        "domain-1",
        allow_return_sql=True,
        history=[{"role": "user", "content": "支付订单按渠道统计"}],
    )

    assert result["status"] == "planned"
    assert "status" in result["generatedSql"]
