from __future__ import annotations

from text2sql_runtime.business_semantics import BusinessSemanticIndex, resolve_business_semantics_path
from text2sql_runtime.conversation import contextualize_question
from text2sql_runtime.conversation_rewrite import build_follow_up_rewrite_context
from text2sql_runtime.models import QueryInput
from text2sql_runtime.schema import SchemaCatalog


def test_contextualize_chart_type_follow_up_from_channel_amount_pie():
    rewritten, log = contextualize_question(
        "折线图也生成一下",
        [
            {"role": "user", "content": "生成一份支付渠道金额分布饼图"},
            {"role": "assistant", "content": "已按您要求的饼图展示：支付渠道分布如下。"},
            {"role": "user", "content": "折线图也生成一下"},
        ],
    )

    assert rewritten == "生成一份支付渠道金额分布折线图"
    assert log is not None
    assert log.get("rewriteReason") == "chart_type_follow_up"


def test_contextualize_bare_radar_follow_up_from_assistant_topic():
    rewritten, log = contextualize_question(
        "雷达图",
        [
            {"role": "user", "content": "查询一下"},
            {
                "role": "assistant",
                "content": "已按您要求的饼图展示：支付渠道分布合计 100，统计如下。",
            },
            {"role": "user", "content": "雷达图"},
        ],
    )

    assert rewritten == "生成一份支付渠道分布雷达图"
    assert log is not None
    assert log.get("rewriteReason") == "chart_type_follow_up"


def test_contextualize_short_follow_up_with_previous_subject():
    rewritten, log = contextualize_question(
        "那按状态呢",
        [{"role": "user", "content": "支付订单按渠道统计"}],
    )

    assert rewritten == "支付订单按状态统计"
    assert log is not None
    assert log["rewriteReason"] == "dimension_slot_follow_up"


def test_contextualize_dimension_follow_up_uses_schema_and_template_dimensions(project_root):
    catalog = SchemaCatalog.from_whitelist(project_root / "configs" / "whitelist_tables.yaml")
    business_semantics = BusinessSemanticIndex.from_config(resolve_business_semantics_path(project_root))
    rewrite_context = build_follow_up_rewrite_context(catalog, business_semantics)

    rewritten, log = contextualize_question(
        "按供应商呢",
        [{"role": "user", "content": "IT资产按状态统计"}],
        rewrite_context,
    )

    assert rewritten == "IT资产按供应商统计"
    assert log is not None
    assert log["rewriteReason"] == "dimension_slot_follow_up"


def test_contextualize_count_to_list_follow_up_generalized():
    rewritten, log = contextualize_question(
        "有哪些",
        [
            {"role": "user", "content": "商户有多少"},
            {"role": "assistant", "content": "商户共 128 家。"},
            {"role": "user", "content": "有哪些"},
        ],
    )

    assert rewritten == "商户有哪些"
    assert log is not None
    assert log["rewriteReason"] == "count_to_list_follow_up"


def test_contextualize_value_substitution_follow_up():
    rewritten, log = contextualize_question(
        "那北京的呢",
        [
            {"role": "user", "content": "上海已分配的IT资产明细"},
            {"role": "assistant", "content": "已返回上海已分配IT资产明细。"},
            {"role": "user", "content": "那北京的呢"},
        ],
    )

    assert rewritten == "北京已分配的IT资产明细"
    assert log is not None
    assert log["rewriteReason"] == "value_substitution_follow_up"


def test_contextualize_value_substitution_follow_up_with_longer_replacement():
    rewritten, log = contextualize_question(
        "那乌鲁木齐的呢",
        [
            {"role": "user", "content": "上海已分配的IT资产明细"},
            {"role": "assistant", "content": "已返回上海已分配IT资产明细。"},
            {"role": "user", "content": "那乌鲁木齐的呢"},
        ],
    )

    assert rewritten == "乌鲁木齐已分配的IT资产明细"
    assert log is not None
    assert log["rewriteReason"] == "value_substitution_follow_up"


def test_contextualize_value_substitution_follow_up_uses_prior_effective_question():
    rewritten, log = contextualize_question(
        "那乌鲁木齐的呢",
        [
            {"role": "user", "content": "上海已分配的IT资产明细"},
            {"role": "assistant", "content": "已返回上海已分配IT资产明细。"},
            {"role": "user", "content": "那北京的呢"},
            {"role": "assistant", "content": "已返回北京已分配IT资产明细。"},
            {"role": "user", "content": "那乌鲁木齐的呢"},
        ],
    )

    assert rewritten == "乌鲁木齐已分配的IT资产明细"
    assert log is not None
    assert log["rewriteReason"] == "value_substitution_follow_up"


def test_contextualize_value_substitution_follow_up_preserves_other_qualifiers():
    rewritten, log = contextualize_question(
        "已分配的呢",
        [
            {"role": "user", "content": "北京库存的IT资产明细"},
            {"role": "assistant", "content": "已返回北京库存IT资产明细。"},
            {"role": "user", "content": "已分配的呢"},
        ],
    )

    assert rewritten == "北京已分配的IT资产明细"
    assert log is not None
    assert log["rewriteReason"] == "value_substitution_follow_up"


def test_contextualize_value_substitution_follow_up_switches_back_to_location_after_status():
    rewritten, log = contextualize_question(
        "那上海的呢",
        [
            {"role": "user", "content": "北京库存的IT资产明细"},
            {"role": "assistant", "content": "已返回北京库存IT资产明细。"},
            {"role": "user", "content": "已分配的呢"},
            {"role": "assistant", "content": "已返回北京已分配IT资产明细。"},
            {"role": "user", "content": "那上海的呢"},
        ],
    )

    assert rewritten == "上海已分配的IT资产明细"
    assert log is not None
    assert log["rewriteReason"] == "value_substitution_follow_up"


def test_query_uses_history_for_follow_up_grouping(service):
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
