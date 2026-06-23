from __future__ import annotations

from pathlib import Path

from text2sql_runtime.context import SchemaContextBuilder
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.semantic_enrichment import SemanticEnrichmentIndex
from text2sql_runtime.semantics import SemanticIndex


def test_context_includes_enrichment_notes_for_phone_lookup(project_root: Path):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    semantics = SemanticIndex.from_config(project_root / "configs" / "semantic_overrides.yaml")
    enrichment = SemanticEnrichmentIndex.from_config(project_root / "configs" / "entity_enrichment.yaml")
    context = SchemaContextBuilder(
        catalog,
        semantics,
        allow_sensitive_fields=True,
        enrichment=enrichment,
    ).build("有手机号13800138000的支付订单吗", ["payment_order"])

    assert "Business enrichment notes" in context
    assert "payment_order.payer_mobile" in context
    assert "SM4" in context


def test_enrichment_context_lines_respects_max_lines(project_root: Path):
    enrichment = SemanticEnrichmentIndex.from_config(project_root / "configs" / "entity_enrichment.yaml")
    lines = enrichment.context_lines("支付订单按渠道统计", ["payment_order"], max_lines=3)
    assert len(lines) <= 3
    assert any("payment_order" in line or "channel" in line for line in lines)
