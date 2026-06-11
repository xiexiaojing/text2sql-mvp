from __future__ import annotations

from text2sql_runtime.visualization import maybe_build_chart


def test_payment_channel_pie_chart():
    rows = [
        {"channel_value": "wechat", "total": 120},
        {"channel_value": "alipay", "total": 80},
    ]
    answer, option = maybe_build_chart(
        "payment_channel_amount_distribution",
        rows,
        {},
        question="生成一份支付渠道金额分布饼图",
    )

    assert answer is not None
    assert option is not None
    assert option["series"][0]["type"] == "pie"


def test_refund_daily_trend_line_chart():
    rows = [
        {"refund_date": "2026-06-01", "total": 3},
        {"refund_date": "2026-06-02", "total": 5},
    ]
    answer, option = maybe_build_chart(
        "refund_daily_trend",
        rows,
        {},
        question="近7天每日退款笔数趋势折线图",
    )

    assert answer is not None
    assert option is not None
    assert option["series"][0]["type"] == "line"
