from __future__ import annotations

from text2sql_api import main
from fastapi.testclient import TestClient
from text2sql_runtime.context import SchemaContextBuilder
from text2sql_runtime.memory import MemoryRecord, SQLiteMemoryStore, score_memory_relevance
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.semantics import SemanticIndex


def test_confirm_and_retrieve_domain_memory(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "audit.sqlite3")
    store.create(
        content="大额订单默认指单笔金额>=10000元",
        scope="domain",
        domain_id="domain-1",
        kind="caliber",
        title="大额订单口径",
        keywords=["大额", "订单", "10000"],
        confirmed_by="user-1",
    )

    hits = store.retrieve(
        question="今天大额订单有多少",
        domain_id="domain-1",
        user_id="user-1",
        limit=3,
    )

    assert len(hits) == 1
    assert "10000" in hits[0].content
    assert score_memory_relevance("大额订单统计", hits[0]) >= 1.0


def test_memory_context_is_injected(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    memories = [
        MemoryRecord(
            memory_id="m1",
            created_at=1,
            updated_at=1,
            scope="domain",
            kind="mapping",
            content="渠道A在系统里对应 channel=wechat",
            title="渠道映射",
            domain_id="domain-1",
            keywords=("渠道A", "wechat"),
        )
    ]
    context = SchemaContextBuilder(catalog, semantics).build(
        "渠道A订单有多少",
        ["payment_order"],
        memories=memories,
    )

    assert "Confirmed business memories" in context
    assert "channel=wechat" in context


def test_query_applies_confirmed_memory(monkeypatch, service):
    service.confirm_memory(
        content="退款口径默认只统计已成功退款",
        scope="domain",
        domain_id="domain-1",
        kind="caliber",
        title="退款口径",
        keywords=["退款", "成功"],
        confirmed_by="user-1",
    )

    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)
    response = client.post(
        "/v1/query",
        json={
            "question": "今天退款有多少",
            "domainId": "domain-1",
            "userId": "user-1",
            "allowReturnSql": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload.get("appliedMemories")
    assert any(
        item.get("kind") == "memory" and item.get("count", 0) >= 1
        for item in payload.get("interactionLogs") or []
    )


def test_memory_confirm_and_list_api(monkeypatch, service):
    monkeypatch.setattr(main, "service", lambda: service)
    client = TestClient(main.app)

    created = client.post(
        "/v1/memories/confirm",
        json={
            "content": "测试商户映射 merchant_id=abc123",
            "scope": "domain",
            "domainId": "domain-1",
            "kind": "mapping",
            "title": "测试商户",
            "keywords": ["测试商户", "merchant_id"],
            "confirmedBy": "user-1",
        },
    )
    assert created.status_code == 200
    memory_id = created.json()["memoryId"]

    listed = client.get("/v1/memories", params={"domainId": "domain-1"})
    assert listed.status_code == 200
    assert listed.json()["count"] >= 1

    deleted = client.delete(f"/v1/memories/{memory_id}")
    assert deleted.status_code == 200
