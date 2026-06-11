from __future__ import annotations

from text2sql_runtime.models import QueryInput


def test_estimate_rejects_missing_domain(service):
    result = service.estimate("支付订单总数是多少", None)

    assert result.status == "rejected"
    assert "domainId" in (result.rejection_reason or "")


def test_estimate_allows_sensitive_search(service):
    result = service.estimate("有手机号13800138000的订单吗", "domain-1")

    assert result.status == "accepted"
    assert result.semantic_plan is not None


def test_query_dry_run_generates_guarded_sql_and_audit(service):
    result = service.query(
        QueryInput(
            question="支付订单总数是多少",
            domain_id="domain-1",
            user_id="user-1",
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.generated_sql is not None
    assert "payment_order" in result.generated_sql
    assert "tenant_id" in result.generated_sql
    assert result.semantic_plan is not None
    assert result.semantic_plan["intent"] == "payment_order_count"
    assert result.answer is not None

    audit = service.audit(result.query_id)
    assert audit is not None
    assert audit["question"] == "支付订单总数是多少"
    assert audit["status"] == "planned"
    assert audit["sql"] == result.generated_sql


def test_query_payment_channel_stat(service):
    result = service.query(
        QueryInput(
            question="支付订单按渠道统计",
            domain_id="domain-1",
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.generated_sql is not None
    assert "payment_order" in result.generated_sql
    assert "channel" in result.generated_sql
    assert result.semantic_plan["intent"] == "payment_channel_stat"


def test_query_follow_up_status_after_channel(service):
    result = service.query(
        QueryInput(
            question="那按状态呢",
            domain_id="domain-1",
            history=[{"role": "user", "content": "支付订单按渠道统计"}],
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.generated_sql is not None
    assert "payment_order" in result.generated_sql
    assert "status" in result.generated_sql
    assert result.semantic_plan["intent"] == "payment_status_stat"


def test_query_rejects_unconfigured_question(service):
    result = service.query(
        QueryInput(
            question="请统计火星基地飞船泊位能耗",
            domain_id="domain-1",
            allow_return_sql=True,
        )
    )

    assert result.status == "rejected"
    assert result.rejection_reason is not None


def test_query_phone_lookup_uses_parameterized_filter(service_with_sensitive_fields):
    result = service_with_sensitive_fields.query(
        QueryInput(
            question="有手机号13800138000的订单吗",
            domain_id="domain-1",
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.generated_sql is not None
    assert "13800138000" not in result.generated_sql
    assert "payer_mobile = %(phone)s" in result.generated_sql
    assert result.semantic_plan["slots"]["phone"] == "13800138000"


def test_query_sensitive_fields_when_enabled(service_with_sensitive_fields):
    result = service_with_sensitive_fields.query(
        QueryInput(
            question="查询支付订单付款手机号",
            domain_id="domain-1",
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.generated_sql is not None
    assert "payer_mobile" in result.generated_sql


def test_schema_summary_lists_payment_intents(service):
    summary = service.schema_summary()

    assert summary["table_count"] >= 3
    intent_ids = {item["id"] for item in summary["business_intents"]}
    assert "payment_order_count" in intent_ids
    assert "payment_channel_stat" in intent_ids


def test_chart_follow_up_rewrite(service):
    result = service.query(
        QueryInput(
            question="折线图也生成一下",
            domain_id="domain-1",
            history=[
                {"role": "user", "content": "生成一份支付渠道金额分布饼图"},
                {"role": "assistant", "content": "已按您要求的饼图展示：支付渠道分布如下。"},
            ],
            allow_return_sql=True,
        )
    )

    assert result.status == "planned"
    assert result.semantic_plan is not None
    assert result.semantic_plan["intent"] == "payment_channel_amount_distribution"
