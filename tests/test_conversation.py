from __future__ import annotations

from text2sql_runtime.conversation import contextualize_question
from text2sql_runtime.models import QueryInput


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
