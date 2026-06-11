from __future__ import annotations

import pytest
from text2sql_runtime.config import load_settings
from text2sql_runtime.executor import parse_mcp_table_text, prepare_pymysql_sql, render_sql_params
from text2sql_runtime.models import RejectedQuery
from text2sql_runtime.schema import SchemaCatalog
from text2sql_runtime.sql_guard import SqlGuard
from text2sql_runtime.sql_policy import ensure_limit, inject_domain_filter


@pytest.fixture()
def guard(project_root):
    settings = load_settings(project_root)
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    return SqlGuard(catalog, settings.performance["allowed_functions"])


def test_injects_domain_filter_for_scoped_table(project_root, guard):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    sql, params = inject_domain_filter(
        "SELECT COUNT(*) AS total FROM payment_order po",
        catalog,
        "domain-1",
    )

    assert params == {"domain_id": "domain-1"}
    assert "po.tenant_id = %(domain_id)s" in sql
    guard.validate(sql)


def test_adds_limit_to_detail_query(project_root, guard):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    sql, _ = inject_domain_filter(
        "SELECT po.id, po.channel FROM payment_order po",
        catalog,
        "domain-1",
    )
    limited = ensure_limit(sql, default_limit=200, max_limit=1000)

    assert limited.endswith("LIMIT 200")
    guard.validate(limited)


def test_injects_domain_filter_even_when_tenant_id_is_selected(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")

    sql, _ = inject_domain_filter(
        "SELECT po.id, po.tenant_id, po.channel FROM payment_order po LIMIT 200",
        catalog,
        "domain-1",
    )

    assert "WHERE po.tenant_id = %(domain_id)s" in sql


def test_rejects_selected_tenant_id_without_domain_filter(guard):
    with pytest.raises(RejectedQuery, match="缺少 domainId"):
        guard.validate("SELECT po.id, po.tenant_id FROM payment_order po LIMIT 10")


def test_rejects_non_select(guard):
    with pytest.raises(RejectedQuery, match="只允许 SELECT"):
        guard.validate("UPDATE payment_order SET status = 'x' WHERE id = '1'")


def test_rejects_history_table(guard):
    with pytest.raises(RejectedQuery, match="审计历史表"):
        guard.validate(
            "SELECT COUNT(*) FROM payment_order_standard_history h WHERE h.tenant_id = %(domain_id)s"
        )


def test_rejects_unknown_table(guard):
    with pytest.raises(RejectedQuery, match="表不在白名单"):
        guard.validate("SELECT COUNT(*) FROM unknown_table u WHERE u.tenant_id = %(domain_id)s")


def test_rejects_sensitive_column(guard):
    with pytest.raises(RejectedQuery, match="敏感字段"):
        guard.validate(
            "SELECT po.payer_mobile FROM payment_order po WHERE po.tenant_id = %(domain_id)s LIMIT 10"
        )


def test_allows_sensitive_column_when_enabled(project_root):
    settings = load_settings(project_root)
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    guard = SqlGuard(
        catalog,
        settings.performance["allowed_functions"],
        allow_sensitive_fields=True,
    )

    result = guard.validate(
        "SELECT po.id, po.payer_mobile FROM payment_order po WHERE po.tenant_id = %(domain_id)s LIMIT 10"
    )

    assert result.tables == ["payment_order"]


def test_allows_parameter_placeholders_in_filters(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    guard = SqlGuard(
        catalog,
        allowed_functions=load_settings(project_root).performance.get("allowed_functions", []),
        allow_sensitive_fields=True,
    )

    result = guard.validate(
        "SELECT COUNT(*) AS total FROM payment_order po "
        "WHERE po.payer_mobile = %(phone)s AND po.tenant_id = %(domain_id)s"
    )

    assert result.tables == ["payment_order"]


def test_rejects_missing_domain_filter(guard):
    with pytest.raises(RejectedQuery, match="缺少 domainId"):
        guard.validate("SELECT po.id FROM payment_order po LIMIT 10")


def test_rejects_unapproved_join_path(guard):
    with pytest.raises(RejectedQuery, match="join 路径"):
        guard.validate(
            "SELECT COUNT(*) FROM merchant m JOIN refund_order ro ON m.id = ro.payment_order_id "
            "WHERE m.tenant_id = %(domain_id)s AND ro.tenant_id = %(domain_id)s"
        )


def test_allows_whitelisted_join_path(guard):
    result = guard.validate(
        "SELECT m.name, SUM(po.amount) AS total FROM merchant m "
        "JOIN payment_order po ON po.merchant_id = m.id "
        "WHERE m.tenant_id = %(domain_id)s AND po.tenant_id = %(domain_id)s "
        "GROUP BY m.id, m.name LIMIT 10"
    )

    assert "merchant" in result.tables
    assert "payment_order" in result.tables


def test_render_sql_params_quotes_mcp_query_params():
    rendered = render_sql_params(
        "SELECT * FROM payment_order WHERE tenant_id = %(domain_id)s",
        {"domain_id": "demo-tenant-1"},
    )

    assert rendered == "SELECT * FROM payment_order WHERE tenant_id = 'demo-tenant-1'"


def test_prepare_pymysql_sql_escapes_literal_like_wildcards():
    sql = (
        "SELECT COUNT(*) AS total FROM payment_order po "
        "WHERE po.status LIKE '%paid%' AND po.tenant_id = %(domain_id)s"
    )
    prepared = prepare_pymysql_sql(sql)
    rendered = prepared % {"domain_id": "domain-1"}
    assert "LIKE '%paid%'" in rendered
    assert "tenant_id = domain-1" in rendered


def test_parse_mcp_table_text_coerces_basic_values():
    columns, rows = parse_mcp_table_text("status,total\n待处理,12\n已完成,34\n")

    assert columns == ["status", "total"]
    assert rows == [{"status": "待处理", "total": 12}, {"status": "已完成", "total": 34}]


def test_parse_mcp_explain_text_repairs_unquoted_possible_keys():
    text = (
        "id,select_type,table,partitions,type,possible_keys,key,key_len,ref,rows,filtered,Extra\n"
        "1,SIMPLE,po,None,ref,idx_a,idx_b,idx_tenant,idx_tenant,1022,const,394,100.0,Using index\n"
    )

    _, rows = parse_mcp_table_text(text)

    assert rows == [
        {
            "id": 1,
            "select_type": "SIMPLE",
            "table": "po",
            "partitions": None,
            "type": "ref",
            "possible_keys": "idx_a,idx_b,idx_tenant",
            "key": "idx_tenant",
            "key_len": 1022,
            "ref": "const",
            "rows": 394,
            "filtered": 100.0,
            "Extra": "Using index",
        }
    ]
