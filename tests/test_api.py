from __future__ import annotations

from text2sql_api import main
from fastapi.testclient import TestClient
from text2sql_runtime.rejection_reasons import UNCONFIGURED_SEMANTIC_REASON


def test_query_endpoint(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/query",
        json={
            "question": "支付订单总数是多少",
            "domainId": "domain-1",
            "userId": "user-1",
            "allowReturnSql": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert "payment_order" in payload["generatedSql"]
    assert payload["semanticPlan"]["intent"] == "payment_order_count"


def test_estimate_endpoint(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)

    response = client.post(
        "/v1/query/estimate",
        json={"question": "有手机号13800138000的订单吗", "domainId": "domain-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["semanticPlan"]["intent"] == "payment_phone_lookup"


def test_audit_endpoint(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)
    created = client.post(
        "/v1/query",
        json={"question": "支付订单按状态统计", "domainId": "domain-1", "allowReturnSql": True},
    ).json()

    response = client.get(f"/v1/audit/{created['queryId']}")

    assert response.status_code == 200
    assert response.json()["query_id"] == created["queryId"]


def test_unsupported_audit_collection_endpoint(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)
    question = "请统计火星基地飞船泊位能耗"
    service.audit_store.record(
        {
            "query_id": "unsupported-1",
            "created_at": 1000,
            "user_id": "user-1",
            "domain_id": "domain-1",
            "question": question,
            "status": "rejected",
            "hit_path": "rejected",
            "rejection_reason": UNCONFIGURED_SEMANTIC_REASON,
        }
    )

    response = client.get("/v1/audit/unsupported")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert payload["items"][0]["question"] == question


def test_schema_summary_endpoint(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)

    response = client.get("/v1/schema/summary")

    assert response.status_code == 200
    assert any(item["id"] == "payment_order_count" for item in response.json()["business_intents"])
