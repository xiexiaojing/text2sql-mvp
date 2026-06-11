from __future__ import annotations

from pathlib import Path

from text2sql_runtime.business_semantics import BusinessSemanticIndex


def test_payment_order_count_intent(project_root: Path):
    semantics = BusinessSemanticIndex.from_config(project_root / "configs" / "business_semantics.yaml")

    plan = semantics.plan("支付订单总数是多少")
    generated = semantics.compile(plan)

    assert plan.status == "executable"
    assert plan.intent == "payment_order_count"
    assert "COUNT(*) AS total" in generated.sql
    assert "payment_order" in generated.sql


def test_payment_channel_stat_intent(project_root: Path):
    semantics = BusinessSemanticIndex.from_config(project_root / "configs" / "business_semantics.yaml")

    plan = semantics.plan("支付订单按渠道统计")
    generated = semantics.compile(plan)

    assert plan.status == "executable"
    assert plan.intent == "payment_channel_stat"
    assert "GROUP BY" in generated.sql
    assert "channel" in generated.sql


def test_unconfigured_intent_is_needs_mapping(project_root: Path):
    semantics = BusinessSemanticIndex.from_config(project_root / "configs" / "business_semantics.yaml")

    plan = semantics.plan("请统计火星基地飞船泊位能耗")

    assert plan.status == "needs_mapping"
    assert plan.intent == "unconfigured_demo"
