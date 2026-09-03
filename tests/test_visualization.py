from __future__ import annotations

import json
from decimal import Decimal

import pytest
from text2sql_runtime.visualization import (
    CHART_TYPE_KEYWORDS,
    CHART_TYPE_LABELS,
    INTENT_DEFAULT_CHART,
    ChartType,
    append_echarts_fence,
    detect_requested_chart_type,
    maybe_build_chart,
    maybe_build_generic_distribution_chart,
    resolve_chart_type,
)

# ---- fixtures ------------------------------------------------------------

CHANNEL_ROWS = [
    {"channel_value": "wechat", "total": 120},
    {"channel_value": "alipay", "total": 80},
    {"channel_value": "unionpay", "total": 40},
]

STATUS_ROWS = [
    {"status_value": "paid", "total": 200},
    {"status_value": "refunded", "total": 15},
]

TREND_ROWS = [
    {"refund_date": "2026-06-01", "total": 3},
    {"refund_date": "2026-06-02", "total": 5},
    {"refund_date": "2026-06-03", "total": 8},
]

MERCHANT_ROWS = [
    {"merchant_name": "喵星饭堂", "total": 999},
    {"merchant_name": "汪汪超市", "total": 420},
]

ALL_CHART_TYPES: tuple[ChartType, ...] = tuple(chart_type for chart_type, _ in CHART_TYPE_KEYWORDS)


# ---- detect_requested_chart_type -----------------------------------------


@pytest.mark.parametrize("chart_type,keywords", list(CHART_TYPE_KEYWORDS))
def test_detect_each_chart_type_by_every_keyword(chart_type: ChartType, keywords: tuple[str, ...]):
    for keyword in keywords:
        question = f"帮我画一个{keyword}"
        assert detect_requested_chart_type(question) == chart_type, (chart_type, keyword)


def test_detect_returns_none_when_no_keyword():
    assert detect_requested_chart_type("支付订单总数是多少") is None


def test_detect_returns_none_for_empty_or_none():
    assert detect_requested_chart_type(None) is None
    assert detect_requested_chart_type("") is None
    assert detect_requested_chart_type("   ") is None


def test_detect_longer_keyword_wins_over_shorter():
    # 「柱状趋势图」contains 2-char "柱状" (bar) and 3-char "趋势图" (line).
    # Longer keyword wins → line.
    assert detect_requested_chart_type("柱状趋势图") == "line"


def test_detect_tie_broken_by_priority_order():
    # 「柱图折线」ties bar («柱图», 2) and line («折线», 2).
    # In CHART_TYPE_KEYWORDS, line is declared before bar, so line wins the tie.
    assert detect_requested_chart_type("柱图折线") == "line"


def test_detect_specific_beats_generic_on_tie():
    # rose keyword「玫瑰图」(3) and bar「柱状图」(3) tie at length 3.
    # rose sits earlier in CHART_TYPE_KEYWORDS → rose wins.
    assert detect_requested_chart_type("柱状图玫瑰图") == "rose"


def test_detect_handles_english_heatmap_keyword():
    assert detect_requested_chart_type("show me a heatmap please") == "heatmap"


# ---- resolve_chart_type --------------------------------------------------


def test_resolve_uses_intent_default_when_no_keyword():
    assert resolve_chart_type("看看情况", "refund_daily_trend") == "line"
    assert resolve_chart_type("看看情况", "merchant_payment_rank") == "bar"


def test_resolve_prefers_explicit_keyword_over_intent_default():
    assert resolve_chart_type("我要漏斗图", "refund_daily_trend") == "funnel"


def test_resolve_returns_none_when_intent_missing_and_no_keyword():
    assert resolve_chart_type("你好", None) is None
    assert resolve_chart_type("你好", "unknown_intent") is None


# ---- maybe_build_chart: guard rails --------------------------------------


def test_maybe_build_chart_returns_none_without_intent():
    assert maybe_build_chart(None, CHANNEL_ROWS, {}, question="饼图") == (None, None)


def test_maybe_build_chart_returns_none_for_non_chart_intent():
    assert maybe_build_chart("payment_order_count", CHANNEL_ROWS, {}, question="饼图") == (None, None)


def test_maybe_build_chart_returns_none_with_empty_rows():
    assert maybe_build_chart("payment_channel_stat", [], {}, question="饼图") == (None, None)


def test_maybe_build_chart_returns_none_when_rows_dont_match_spec():
    # payment_channel_stat expects channel_value/status_value columns.
    rows = [{"other_col": "x", "total": 1}]
    assert maybe_build_chart("payment_channel_stat", rows, {}, question="饼图") == (None, None)


# ---- maybe_build_chart: series[0].type across every requested type -------


def _series_type(option: dict) -> str:
    return option["series"][0]["type"]


# For channel/status distribution intents, every viz-sensible type maps to an
# echarts series type. gantt / sankey are not enabled for these builders and
# fall back to pie.
CHANNEL_TYPE_MATRIX: tuple[tuple[str, str, str], ...] = (
    ("pie", "饼图", "pie"),
    ("donut", "环形图", "pie"),
    ("rose", "玫瑰图", "pie"),
    ("bar", "柱状图", "bar"),
    ("horizontal_bar", "条形图", "bar"),
    ("line", "折线图", "line"),
    ("area", "面积图", "line"),
    ("scatter", "散点图", "scatter"),
    ("bubble", "气泡图", "scatter"),
    ("radar", "雷达图", "radar"),
    ("heatmap", "热力图", "heatmap"),
    ("funnel", "漏斗图", "funnel"),
    ("waterfall", "瀑布图", "bar"),
    ("boxplot", "箱线图", "boxplot"),
)


@pytest.mark.parametrize("requested,keyword,series_type", CHANNEL_TYPE_MATRIX)
def test_channel_distribution_series_type(requested: str, keyword: str, series_type: str):
    answer, option = maybe_build_chart(
        "payment_channel_stat",
        CHANNEL_ROWS,
        {},
        question=f"支付渠道{keyword}",
    )
    assert answer is not None
    assert option is not None
    assert _series_type(option) == series_type


@pytest.mark.parametrize("requested_type", ["gantt", "sankey"])
def test_channel_distribution_falls_back_to_pie_for_unsupported(requested_type: str):
    label = CHART_TYPE_LABELS[requested_type]  # type: ignore[index]
    answer, option = maybe_build_chart(
        "payment_channel_stat",
        CHANNEL_ROWS,
        {},
        question=f"支付渠道{label}",
    )
    assert option is not None
    assert _series_type(option) == "pie"
    # prefix should note that the current data was rendered differently
    assert answer is not None
    assert "更适合" in answer


TREND_TYPE_MATRIX: tuple[tuple[str, str, str], ...] = (
    ("line", "折线图", "line"),
    ("area", "面积图", "line"),
    ("bar", "柱状图", "bar"),
    ("horizontal_bar", "条形图", "bar"),
)


@pytest.mark.parametrize("requested,keyword,series_type", TREND_TYPE_MATRIX)
def test_refund_trend_series_type(requested: str, keyword: str, series_type: str):
    answer, option = maybe_build_chart(
        "refund_daily_trend",
        TREND_ROWS,
        {},
        question=f"近7天退款{keyword}",
    )
    assert answer is not None
    assert option is not None
    assert _series_type(option) == series_type


@pytest.mark.parametrize("requested_type", ["pie", "radar", "funnel", "heatmap"])
def test_refund_trend_falls_back_to_line_for_unsupported(requested_type: str):
    label = CHART_TYPE_LABELS[requested_type]  # type: ignore[index]
    answer, option = maybe_build_chart(
        "refund_daily_trend",
        TREND_ROWS,
        {},
        question=f"近7天退款{label}",
    )
    assert option is not None
    assert _series_type(option) == "line"


MERCHANT_TYPE_MATRIX: tuple[tuple[str, str, str], ...] = (
    ("bar", "柱状图", "bar"),
    ("horizontal_bar", "条形图", "bar"),
    ("line", "折线图", "line"),
)


@pytest.mark.parametrize("requested,keyword,series_type", MERCHANT_TYPE_MATRIX)
def test_merchant_rank_series_type(requested: str, keyword: str, series_type: str):
    answer, option = maybe_build_chart(
        "merchant_payment_rank",
        MERCHANT_ROWS,
        {},
        question=f"商户{keyword}",
    )
    assert answer is not None
    assert option is not None
    assert _series_type(option) == series_type


@pytest.mark.parametrize("requested_type", ["pie", "radar", "funnel"])
def test_merchant_rank_falls_back_to_bar_for_unsupported(requested_type: str):
    label = CHART_TYPE_LABELS[requested_type]  # type: ignore[index]
    answer, option = maybe_build_chart(
        "merchant_payment_rank",
        MERCHANT_ROWS,
        {},
        question=f"商户{label}",
    )
    assert option is not None
    assert _series_type(option) == "bar"


# ---- default (no keyword) still produces the intent default --------------


def test_channel_stat_default_is_pie_without_keyword():
    _, option = maybe_build_chart("payment_channel_stat", CHANNEL_ROWS, {})
    assert option is not None
    assert _series_type(option) == "pie"


def test_status_stat_default_is_bar_without_keyword():
    _, option = maybe_build_chart("payment_status_stat", STATUS_ROWS, {})
    assert option is not None
    assert _series_type(option) == "bar"


def test_refund_trend_default_is_line_without_keyword():
    _, option = maybe_build_chart("refund_daily_trend", TREND_ROWS, {})
    assert option is not None
    assert _series_type(option) == "line"


def test_merchant_rank_default_is_bar_without_keyword():
    _, option = maybe_build_chart("merchant_payment_rank", MERCHANT_ROWS, {})
    assert option is not None
    assert _series_type(option) == "bar"


# ---- INTENT_DEFAULT_CHART covers every declared CHART_INTENTS entry ------


def test_every_chart_intent_has_a_default_chart():
    from text2sql_runtime.visualization import CHART_INTENTS

    missing = CHART_INTENTS - set(INTENT_DEFAULT_CHART)
    assert missing == set(), f"CHART_INTENTS missing defaults: {missing}"


# ---- edge cases on rows --------------------------------------------------


def test_single_row_still_produces_chart():
    _, option = maybe_build_chart(
        "payment_channel_stat",
        [{"channel_value": "wechat", "total": 42}],
        {},
        question="饼图",
    )
    assert option is not None
    assert option["series"][0]["data"] == [{"name": "wechat", "value": 42}]


def test_null_label_replaced_by_empty_placeholder():
    rows = [
        {"channel_value": None, "total": 5},
        {"channel_value": "wechat", "total": 20},
    ]
    _, option = maybe_build_chart("payment_channel_stat", rows, {}, question="饼图")
    assert option is not None
    labels = [item["name"] for item in option["series"][0]["data"]]
    assert "未填写" in labels


def test_zero_values_do_not_crash_radar_or_bubble():
    zero_rows = [{"channel_value": "wechat", "total": 0}, {"channel_value": "alipay", "total": 0}]
    _, radar_option = maybe_build_chart("payment_channel_stat", zero_rows, {}, question="雷达图")
    assert radar_option is not None
    # radar indicator max must be at least 1 to be a valid axis
    for indicator in radar_option["radar"]["indicator"]:
        assert indicator["max"] >= 1

    _, bubble_option = maybe_build_chart("payment_channel_stat", zero_rows, {}, question="气泡图")
    assert bubble_option is not None
    # bubble with all-zero max degrades to scatter — still a valid chart
    assert _series_type(bubble_option) == "scatter"


# ---- decimal totals must NOT be silently truncated -----------------------


def test_decimal_amounts_are_preserved_not_truncated():
    """RED-first: SUM(amount) returns Decimal for money columns; the chart
    must not silently drop the fractional part (previous code called
    ``int(row.get(value_key) or 0)`` and lost 0.5 per row).
    """
    rows = [
        {"channel_value": "wechat", "total": Decimal("12.50")},
        {"channel_value": "alipay", "total": Decimal("7.25")},
    ]
    _, option = maybe_build_chart(
        "payment_channel_amount_distribution",
        rows,
        {},
        question="支付渠道金额饼图",
    )
    assert option is not None
    values = [item["value"] for item in option["series"][0]["data"]]
    # numeric equality — 12 or 12.0 must NOT masquerade as 12.5
    assert values[0] == pytest.approx(12.5)
    assert values[1] == pytest.approx(7.25)
    assert sum(values) == pytest.approx(19.75)


def test_float_amounts_preserved_via_generic_path():
    rows = [
        {"channel_value": "wechat", "total": 1.5},
        {"channel_value": "alipay", "total": 2.75},
    ]
    _, option = maybe_build_generic_distribution_chart("画个饼图", rows, {})
    assert option is not None
    values = [item["value"] for item in option["series"][0]["data"]]
    assert values == [pytest.approx(1.5), pytest.approx(2.75)]


def test_integer_totals_stay_integer():
    _, option = maybe_build_chart(
        "payment_channel_stat", CHANNEL_ROWS, {}, question="饼图",
    )
    assert option is not None
    values = [item["value"] for item in option["series"][0]["data"]]
    for value in values:
        assert isinstance(value, int)


# ---- maybe_build_generic_distribution_chart ------------------------------


def test_generic_chart_returns_none_without_chart_keyword():
    rows = [{"channel_value": "wechat", "total": 5}]
    assert maybe_build_generic_distribution_chart("支付渠道分布", rows, {}) == (None, None)


def test_generic_chart_returns_none_when_no_value_column():
    rows = [{"channel_value": "wechat"}]
    assert maybe_build_generic_distribution_chart("画个饼图", rows, {}) == (None, None)


def test_generic_chart_two_column_fallback():
    rows = [{"foo": "a", "count": 3}, {"foo": "b", "count": 7}]
    _, option = maybe_build_generic_distribution_chart("画个饼图", rows, {})
    assert option is not None
    assert _series_type(option) == "pie"


def test_generic_chart_ignores_ambiguous_multi_column_rows():
    rows = [{"a": 1, "b": 2, "count": 3}]
    assert maybe_build_generic_distribution_chart("画个饼图", rows, {}) == (None, None)


def test_generic_chart_sankey_option_shape():
    rows = [
        {"channel_value": "wechat", "count": 10},
        {"channel_value": "alipay", "count": 5},
    ]
    _, option = maybe_build_generic_distribution_chart("画个桑基图", rows, {})
    assert option is not None
    assert _series_type(option) == "sankey"
    # source flows should list every non-zero category as a link target
    links = option["series"][0]["links"]
    assert {link["target"] for link in links} == {"wechat", "alipay"}


def test_generic_chart_gantt_uses_horizontal_bar_layout():
    rows = [
        {"channel_value": "wechat", "count": 3},
        {"channel_value": "alipay", "count": 5},
    ]
    _, option = maybe_build_generic_distribution_chart("画个甘特图", rows, {})
    assert option is not None
    assert _series_type(option) == "bar"
    # gantt uses horizontal bar layout — categories on yAxis, values on xAxis
    assert option["yAxis"]["type"] == "category"
    assert option["xAxis"]["type"] == "value"


# ---- append_echarts_fence ------------------------------------------------


def test_append_echarts_fence_wraps_option_in_fence():
    option = {"series": [{"type": "pie", "data": []}]}
    out = append_echarts_fence("你好", option)
    assert out.startswith("你好")
    assert "```echarts" in out
    assert out.endswith("```")
    # payload must be valid JSON
    payload = out.split("```echarts\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(payload) == option


def test_append_echarts_fence_preserves_unicode():
    option = {"title": {"text": "支付渠道"}}
    out = append_echarts_fence("答案", option)
    assert "支付渠道" in out


# ---- prefix wording ------------------------------------------------------


def test_prefix_matches_when_user_request_equals_effective_type():
    answer, _ = maybe_build_chart(
        "payment_channel_stat", CHANNEL_ROWS, {}, question="支付渠道饼图",
    )
    assert answer is not None
    assert "已按您要求的饼图" in answer


def test_prefix_indicates_fallback_when_type_downgraded():
    answer, _ = maybe_build_chart(
        "refund_daily_trend", TREND_ROWS, {}, question="退款饼图",
    )
    assert answer is not None
    # pie is not allowed for trend intent → falls back to line, with a hint
    assert "更适合" in answer


# ---- every ChartType in the Literal has a keyword and a label ------------


def test_every_chart_type_has_a_keyword_and_label():
    keyword_types = {chart_type for chart_type, _ in CHART_TYPE_KEYWORDS}
    label_types = set(CHART_TYPE_LABELS)
    assert keyword_types == label_types
