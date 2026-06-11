from __future__ import annotations

from text2sql_runtime.context import SchemaContextBuilder
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.semantics import SemanticIndex


def test_payment_semantic_context_lists_demo_tables(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    context = SchemaContextBuilder(catalog, semantics).build("支付订单按渠道统计", ["payment_order"])

    assert "payment_order" in context
    assert "refund_order" in context
    assert "merchant" in context
    assert "Semantic concepts:" in context


def test_sensitive_columns_are_in_context_when_enabled(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    context = SchemaContextBuilder(
        catalog,
        semantics,
        allow_sensitive_fields=True,
    ).build("查询支付订单付款手机号", ["payment_order"])

    assert "payer_mobile" in context
    assert "may be used when requested" in context


def test_phone_lookup_context_warns_against_unfiltered_total(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    context = SchemaContextBuilder(
        catalog,
        semantics,
        allow_sensitive_fields=True,
    ).build("有手机号13800138000的订单吗", ["payment_order"])

    assert "filter" in context.lower()
    assert "do not answer with an unfiltered" in context
