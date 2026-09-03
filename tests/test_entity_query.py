from __future__ import annotations

import pytest
from text2sql_runtime.config import load_settings
from text2sql_runtime.entity_query import EntityQueryCompiler
from text2sql_runtime.models import RejectedQuery
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.sql_guard import SqlGuard
from text2sql_runtime.sql_policy import ensure_limit, inject_domain_filter


ENTITY_CONFIG = {
    "payment_order": {
        "table": "payment_order",
        "alias": "po",
        "display_name": "支付订单",
        "attributes": {
            "channel": {
                "kind": "enum",
                "column": "channel",
                "label": "渠道",
                "group_alias": "channel_value",
                "values": {"wechat": ["微信"], "alipay": ["支付宝"]},
            },
        },
        "metric_fields": {
            "amount": {
                "column": "amount",
                "label": "金额",
                "aggregations": ["sum", "avg", "max", "min"],
            },
        },
    },
}


@pytest.fixture()
def compiler() -> EntityQueryCompiler:
    return EntityQueryCompiler.from_config(ENTITY_CONFIG)


def _compile(compiler: EntityQueryCompiler, metric: dict[str, str], **spec_overrides) -> str:
    slots = {
        "entity": "payment_order",
        "entity_query": {"entity": "payment_order", "metric": metric, **spec_overrides},
    }
    return compiler.compile(slots).sql


@pytest.mark.parametrize(
    "agg,expected_fn",
    [("sum", "SUM"), ("avg", "AVG"), ("max", "MAX"), ("min", "MIN")],
)
def test_metric_generates_aggregation_over_whitelisted_column(
    compiler: EntityQueryCompiler, agg: str, expected_fn: str
) -> None:
    sql = _compile(compiler, {"agg": agg, "field": "amount"})
    assert sql == f"SELECT {expected_fn}(po.amount) AS total FROM payment_order po"


def test_grouped_metric_emits_group_by_and_order(compiler: EntityQueryCompiler) -> None:
    sql = _compile(compiler, {"agg": "sum", "field": "amount"}, group_by=["channel"])
    assert (
        sql
        == "SELECT po.channel AS channel_value, SUM(po.amount) AS total "
        "FROM payment_order po GROUP BY channel_value ORDER BY total DESC"
    )


def test_metric_field_outside_whitelist_is_rejected(compiler: EntityQueryCompiler) -> None:
    with pytest.raises(RejectedQuery) as excinfo:
        _compile(compiler, {"agg": "sum", "field": "tenant_id"})
    assert excinfo.value.code == "entity_metric_field_not_allowed"


def test_unknown_aggregation_is_rejected(compiler: EntityQueryCompiler) -> None:
    with pytest.raises(RejectedQuery) as excinfo:
        _compile(compiler, {"agg": "stddev", "field": "amount"})
    assert excinfo.value.code == "entity_metric_not_allowed"


def test_aggregation_not_permitted_for_field_is_rejected() -> None:
    compiler = EntityQueryCompiler.from_config(
        {
            "payment_order": {
                **ENTITY_CONFIG["payment_order"],
                "metric_fields": {
                    "amount": {
                        "column": "amount",
                        "label": "金额",
                        "aggregations": ["sum"],
                    },
                },
            }
        }
    )
    with pytest.raises(RejectedQuery) as excinfo:
        _compile(compiler, {"agg": "avg", "field": "amount"})
    assert excinfo.value.code == "entity_metric_not_allowed"


def test_sum_metric_passes_sql_guard(project_root, compiler: EntityQueryCompiler) -> None:
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    settings = load_settings(project_root)
    guard = SqlGuard(catalog, settings.performance["allowed_functions"])

    raw_sql = _compile(compiler, {"agg": "sum", "field": "amount"}, group_by=["channel"])
    sql_with_domain, _ = inject_domain_filter(raw_sql, catalog, "demo-tenant-1")
    guarded_sql = ensure_limit(sql_with_domain, default_limit=200, max_limit=1000)
    result = guard.validate(guarded_sql)

    assert "SUM(po.amount)" in result.sql
    assert "po.tenant_id" in result.sql
    assert result.tables == ["payment_order"]
