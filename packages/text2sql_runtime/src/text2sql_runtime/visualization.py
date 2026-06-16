from __future__ import annotations

import json
from typing import Any, Literal

from .value_labels import resolve_grouped_label

ChartType = Literal[
    "pie",
    "donut",
    "rose",
    "bar",
    "horizontal_bar",
    "line",
    "area",
    "scatter",
    "bubble",
    "radar",
    "heatmap",
    "funnel",
    "waterfall",
    "boxplot",
    "gantt",
    "sankey",
]

CHART_INTENTS = frozenset(
    {
        "payment_channel_amount_distribution",
        "refund_daily_trend",
        "merchant_payment_rank",
        "payment_channel_stat",
        "payment_status_stat",
    }
)

INTENT_DEFAULT_CHART: dict[str, ChartType] = {
    "payment_channel_amount_distribution": "pie",
    "refund_daily_trend": "line",
    "merchant_payment_rank": "bar",
    "payment_channel_stat": "pie",
    "payment_status_stat": "bar",
}

CHART_TYPE_KEYWORDS: tuple[tuple[ChartType, tuple[str, ...]], ...] = (
    ("rose", ("南丁格尔玫瑰图", "玫瑰图")),
    ("donut", ("甜甜圈图", "环形图", "圆环图")),
    ("sankey", ("桑基图", "桑基")),
    ("waterfall", ("瀑布图",)),
    ("horizontal_bar", ("条形图", "条状图")),
    ("bubble", ("气泡图",)),
    ("scatter", ("散点图",)),
    ("boxplot", ("箱线图", "盒须图")),
    ("funnel", ("漏斗图",)),
    ("gantt", ("甘特图",)),
    ("radar", ("雷达图",)),
    ("heatmap", ("热力图", "热图", "heatmap")),
    ("area", ("面积图",)),
    ("line", ("折线图", "趋势图", "走势图", "曲线图", "时序图", "折线", "趋势", "走势")),
    ("bar", ("柱状图", "柱形图", "直方图", "柱图")),
    ("pie", ("饼图", "圆饼图", "占比图", "扇形图", "饼状图")),
)

CHART_TYPE_LABELS: dict[ChartType, str] = {
    "pie": "饼图",
    "donut": "环形图",
    "rose": "南丁格尔玫瑰图",
    "bar": "柱状图",
    "horizontal_bar": "条形图",
    "line": "折线图",
    "area": "面积图",
    "scatter": "散点图",
    "bubble": "气泡图",
    "radar": "雷达图",
    "heatmap": "热力图",
    "funnel": "漏斗图",
    "waterfall": "瀑布图",
    "boxplot": "箱线图",
    "gantt": "甘特图",
    "sankey": "桑基图",
}

_LINE_SPECS = (
    {"label_key": "refund_date", "value_key": "total", "x_name": "日期", "y_name": "退款笔数"},
)
_PIE_SPECS = (
    {"label_key": "channel_value", "value_key": "total", "title": "支付渠道分布"},
    {"label_key": "status_value", "value_key": "total", "title": "支付订单状态分布"},
)


def detect_requested_chart_type(question: str | None) -> ChartType | None:
    text = str(question or "").strip()
    if not text:
        return None
    best: tuple[int, ChartType] | None = None
    for chart_type, keywords in CHART_TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in text and (best is None or len(keyword) > best[0]):
                best = (len(keyword), chart_type)
    return best[1] if best else None


def resolve_chart_type(question: str | None, intent: str | None) -> ChartType | None:
    requested = detect_requested_chart_type(question)
    if requested is not None:
        return requested
    if intent:
        return INTENT_DEFAULT_CHART.get(intent)
    return None


def maybe_build_chart(
    intent: str | None,
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    question: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    if not intent or intent not in CHART_INTENTS or not rows:
        return None, None

    chart_type = resolve_chart_type(question, intent)
    if chart_type is None:
        return None, None

    if intent == "refund_daily_trend":
        return _build_refund_trend_response(rows, chart_type, question)
    if intent == "merchant_payment_rank":
        return _build_merchant_rank_response(rows, chart_type, question)
    return _build_distribution_response(rows, intent, chart_type, question)


def maybe_build_generic_distribution_chart(
    question: str | None,
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    intent: str | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    chart_type = detect_requested_chart_type(question)
    if chart_type is None or not rows:
        return None, None
    spec = _infer_distribution_spec(rows, slots, intent, question)
    if spec is None:
        return None, None
    label_key, value_key, title = spec
    categories, values = _extract_series(rows, label_key, value_key, empty_label="未填写")
    if not categories:
        return None, None
    effective_type = chart_type
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=title,
        x_name="类别",
        y_name="人数",
        rotate_labels=effective_type in {"bar", "waterfall", "boxplot"},
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    total = sum(values)
    answer = f"{prefix}{title}合计 {total}，统计如下。"
    return answer, option


def append_echarts_fence(answer: str, option: dict[str, Any]) -> str:
    payload = json.dumps(option, ensure_ascii=False, indent=2)
    return f"{answer.rstrip()}\n\n```echarts\n{payload}\n```"


def chart_series_type(option: dict[str, Any] | None) -> str | None:
    if not option:
        return None
    series = option.get("series")
    if not isinstance(series, list) or not series:
        return None
    first = series[0]
    if not isinstance(first, dict):
        return None
    return str(first.get("type") or "")


def _build_refund_trend_response(
    rows: list[dict[str, Any]],
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    spec = _LINE_SPECS[0]
    categories, values = _extract_series(rows, spec["label_key"], spec["value_key"])
    if not categories:
        return None, None
    total = sum(values)
    effective_type = chart_type if chart_type in {"line", "bar", "area", "horizontal_bar"} else "line"
    title = "近7天退款笔数趋势"
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=title,
        x_name=spec["x_name"],
        y_name=spec["y_name"],
        smooth_line=True,
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    answer = f"{prefix}近7天共退款 {total} 笔，按日走势如下。"
    return answer, option


def _build_merchant_rank_response(
    rows: list[dict[str, Any]],
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    categories, values = _extract_series(rows, "merchant_name", "total", empty_label="未知商户")
    if not categories:
        return None, None
    effective_type = chart_type if chart_type in {"bar", "line", "horizontal_bar"} else "bar"
    title = "商户交易金额排名"
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=title,
        x_name="商户",
        y_name="交易金额",
        rotate_labels=True,
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    answer = f"{prefix}{title}如下。"
    return answer, option


def _build_visiting_trend_response(
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    spec = _LINE_SPECS[0]
    categories, values = _extract_series(rows, spec["label_key"], spec["value_key"])
    if not categories:
        return None, None
    person = str(slots.get("person_name") or "该走访人")
    total = sum(values)
    effective_type = chart_type if chart_type in {"line", "bar", "area", "horizontal_bar"} else "line"
    title = f"{person}去年走访数走势"
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=title,
        x_name=spec["x_name"],
        y_name=spec["y_name"],
        smooth_line=True,
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    answer = f"{prefix}{person}去年共走访 {total} 次，按月走势如下。"
    return answer, option


def _build_grid_party_response(
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    buildings: list[str] = []
    values: list[int] = []
    for row in rows:
        label = row.get("building_name") or row.get("building_name_path")
        if label is None:
            continue
        buildings.append(str(label))
        values.append(int(row.get("total") or 0))
    if not buildings:
        return None, None

    grid_label = str(rows[0].get("grid_name") or slots.get("grid_name") or "该网格")
    total = sum(values)
    effective_type = chart_type
    if chart_type not in {"heatmap", "bar"}:
        effective_type = "heatmap"

    if effective_type == "heatmap":
        option = _build_heatmap_option(buildings, values, f"{grid_label}党员楼栋分布")
    else:
        option = _render_distribution_chart(
            chart_type="bar",
            categories=buildings,
            values=values,
            title=f"{grid_label}党员楼栋分布",
            x_name="楼栋",
            y_name="党员数",
            rotate_labels=True,
        )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    answer = f"{prefix}{grid_label}共有 {total} 名党员，按楼栋分布如下。"
    return answer, option


def _build_grid_distribution_response(
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    intent: str,
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    categories, values = _extract_series(rows, "grid_name", "total")
    if not categories:
        return None, None
    total = sum(values)
    tag_label = str(slots.get("tag_name") or "目标人群")
    title = "各网格人口分布" if intent == "grid_population_rank" else f"各网格{tag_label}分布"
    effective_type = chart_type if chart_type in {"bar", "pie", "donut", "rose", "horizontal_bar", "funnel", "radar"} else "bar"
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=title,
        x_name="网格",
        y_name="人数",
        rotate_labels=effective_type == "bar",
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    if intent == "grid_population_rank":
        answer = f"{prefix}各网格人口合计 {total} 人，分布如下。"
    else:
        answer = f"{prefix}各网格「{tag_label}」合计 {total} 人，分布如下。"
    return answer, option


def _build_distribution_response(
    rows: list[dict[str, Any]],
    intent: str,
    chart_type: ChartType,
    question: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    spec = _match_pie_spec(rows)
    if spec is None:
        return None, None
    categories, values = _extract_series(rows, spec["label_key"], spec["value_key"], empty_label="未填写")
    if not categories:
        return None, None
    effective_type = chart_type if chart_type in {
        "pie",
        "donut",
        "rose",
        "bar",
        "horizontal_bar",
        "line",
        "area",
        "radar",
        "funnel",
        "scatter",
        "bubble",
        "waterfall",
        "boxplot",
        "heatmap",
    } else "pie"
    option = _render_distribution_chart(
        chart_type=effective_type,
        categories=categories,
        values=values,
        title=spec["title"],
        x_name="类别",
        y_name="人数",
        rotate_labels=effective_type == "bar",
    )
    if option is None:
        return None, None
    prefix = _chart_answer_prefix(question, effective_type)
    if intent in {"payment_channel_amount_distribution", "payment_channel_stat"}:
        answer = f"{prefix}支付渠道分布如下。"
    elif intent == "payment_status_stat":
        answer = f"{prefix}支付订单状态分布如下。"
    else:
        answer = f"{prefix}{spec['title']}如下。"
    return answer, option


def _chart_answer_prefix(question: str | None, chart_type: ChartType) -> str:
    requested = detect_requested_chart_type(question)
    if requested is None:
        return ""
    label = CHART_TYPE_LABELS.get(requested, CHART_TYPE_LABELS.get(chart_type, "图表"))
    if requested == chart_type:
        return f"已按您要求的{label}展示："
    return f"当前数据更适合{label}展示："


def _render_category_chart(
    *,
    chart_type: ChartType,
    categories: list[str],
    values: list[int],
    title: str,
    x_name: str,
    y_name: str,
    smooth_line: bool = False,
    rotate_labels: bool = False,
) -> dict[str, Any] | None:
    return _render_distribution_chart(
        chart_type=chart_type,
        categories=categories,
        values=values,
        title=title,
        x_name=x_name,
        y_name=y_name,
        smooth_line=smooth_line,
        rotate_labels=rotate_labels,
    )


def _render_distribution_chart(
    *,
    chart_type: ChartType,
    categories: list[str],
    values: list[int],
    title: str,
    x_name: str,
    y_name: str,
    smooth_line: bool = False,
    rotate_labels: bool = False,
) -> dict[str, Any] | None:
    if not categories:
        return None

    data = [{"name": name, "value": value} for name, value in zip(categories, values, strict=True)]
    max_value = max(values) if values else 0

    if chart_type in {"pie", "donut", "rose"}:
        radius = ["36%", "62%"] if chart_type == "donut" else "58%"
        series: dict[str, Any] = {
            "type": "pie",
            "radius": radius,
            "center": ["50%", "46%"],
            "data": data,
            "label": {"formatter": "{b}\n{d}%"},
        }
        if chart_type == "rose":
            series["roseType"] = "area"
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
            "legend": {"orient": "horizontal", "bottom": 0},
            "series": [series],
        }

    if chart_type == "funnel":
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
            "series": [
                {
                    "type": "funnel",
                    "left": "10%",
                    "width": "80%",
                    "sort": "descending",
                    "label": {"show": True, "position": "inside"},
                    "data": data,
                }
            ],
        }

    if chart_type == "radar":
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "radar": {
                "indicator": [{"name": name, "max": max_value or 1} for name in categories],
            },
            "series": [
                {
                    "type": "radar",
                    "data": [{"value": values, "name": title}],
                }
            ],
        }

    if chart_type == "heatmap":
        heatmap_data = [[index, 0, value] for index, value in enumerate(values)]
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"position": "top"},
            "grid": {"height": "56%", "top": "14%", "left": "8%", "right": "8%", "containLabel": True},
            "xAxis": {
                "type": "category",
                "data": categories,
                "splitArea": {"show": True},
                "axisLabel": {"interval": 0, "rotate": 30 if rotate_labels else 0},
            },
            "yAxis": {"type": "category", "data": [y_name], "splitArea": {"show": True}},
            "visualMap": {
                "min": 0,
                "max": max_value or 1,
                "calculable": True,
                "orient": "horizontal",
                "left": "center",
                "bottom": "2%",
            },
            "series": [
                {
                    "type": "heatmap",
                    "data": heatmap_data,
                    "label": {"show": True},
                }
            ],
        }

    if chart_type in {"scatter", "bubble"}:
        if chart_type == "bubble" and max_value:
            bubble_data = [
                {
                    "value": [index, value],
                    "symbolSize": max(12, int(24 * value / max_value)),
                }
                for index, value in enumerate(values)
            ]
            return {
                "title": {"text": title, "left": "center"},
                "tooltip": {"trigger": "item"},
                "grid": {"left": "8%", "right": "4%", "bottom": "16%", "containLabel": True},
                "xAxis": {"type": "category", "data": categories, "name": x_name},
                "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
                "series": [{"type": "scatter", "data": bubble_data}],
            }
        scatter_data = [[index, value] for index, value in enumerate(values)]
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "grid": {"left": "8%", "right": "4%", "bottom": "16%", "containLabel": True},
            "xAxis": {"type": "category", "data": categories, "name": x_name},
            "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
            "series": [{"type": "scatter", "data": scatter_data, "symbolSize": 14}],
        }

    if chart_type in {"line", "area"}:
        area_style = {"opacity": 0.22 if chart_type == "area" else 0.08}
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "axis"},
            "grid": {"left": "8%", "right": "4%", "bottom": "12%", "containLabel": True},
            "xAxis": {"type": "category", "data": categories, "name": x_name},
            "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
            "series": [
                {
                    "type": "line",
                    "smooth": smooth_line,
                    "data": values,
                    "areaStyle": area_style,
                }
            ],
        }

    if chart_type in {"horizontal_bar", "gantt"}:
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": "8%", "right": "8%", "bottom": "8%", "containLabel": True},
            "xAxis": {"type": "value", "name": y_name, "minInterval": 1},
            "yAxis": {"type": "category", "data": categories, "name": x_name, "inverse": True},
            "series": [
                {
                    "type": "bar",
                    "data": values,
                    "label": {"show": True, "position": "right"},
                }
            ],
        }

    if chart_type == "waterfall":
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
            "grid": {"left": "8%", "right": "4%", "bottom": "16%", "containLabel": True},
            "xAxis": {"type": "category", "data": categories, "name": x_name},
            "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
            "series": [
                {
                    "type": "bar",
                    "stack": "total",
                    "data": values,
                    "label": {"show": True, "position": "top"},
                }
            ],
        }

    if chart_type == "boxplot":
        box_data = [[value, value, value, value, value] for value in values]
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "grid": {"left": "8%", "right": "4%", "bottom": "16%", "containLabel": True},
            "xAxis": {"type": "category", "data": categories, "name": x_name},
            "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
            "series": [{"type": "boxplot", "data": box_data}],
        }

    if chart_type == "sankey":
        sankey_links = [
            {"source": title, "target": name, "value": value}
            for name, value in zip(categories, values, strict=True)
            if value > 0
        ]
        sankey_nodes = [{"name": title}] + [{"name": name} for name in categories]
        return {
            "title": {"text": title, "left": "center"},
            "tooltip": {"trigger": "item"},
            "series": [
                {
                    "type": "sankey",
                    "layout": "none",
                    "emphasis": {"focus": "adjacency"},
                    "data": sankey_nodes,
                    "links": sankey_links,
                }
            ],
        }

    axis_label: dict[str, Any] = {"interval": 0}
    if rotate_labels:
        axis_label["rotate"] = 30
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis", "axisPointer": {"type": "shadow"}},
        "grid": {"left": "8%", "right": "4%", "bottom": "16%", "containLabel": True},
        "xAxis": {"type": "category", "data": categories, "name": x_name, "axisLabel": axis_label},
        "yAxis": {"type": "value", "name": y_name, "minInterval": 1},
        "series": [
            {
                "type": "bar",
                "data": values,
                "label": {"show": True, "position": "top"},
            }
        ],
    }


def _extract_series(
    rows: list[dict[str, Any]],
    label_key: str,
    value_key: str,
    *,
    empty_label: str | None = None,
) -> tuple[list[str], list[int]]:
    categories: list[str] = []
    values: list[int] = []
    for row in rows:
        label = row.get(label_key)
        if label is None or str(label).strip() == "":
            if empty_label is None:
                continue
            label = empty_label
        categories.append(str(resolve_grouped_label(label_key, label)))
        values.append(int(row.get(value_key) or 0))
    return categories, values


def _build_heatmap_option(
    buildings: list[str],
    values: list[int],
    title: str,
) -> dict[str, Any] | None:
    if not buildings:
        return None
    max_value = max(values) if values else 0
    heatmap_data = [[index, 0, value] for index, value in enumerate(values)]
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"position": "top"},
        "grid": {"height": "56%", "top": "14%", "left": "8%", "right": "8%", "containLabel": True},
        "xAxis": {
            "type": "category",
            "data": buildings,
            "splitArea": {"show": True},
            "axisLabel": {"interval": 0, "rotate": 30},
        },
        "yAxis": {"type": "category", "data": ["党员数"], "splitArea": {"show": True}},
        "visualMap": {
            "min": 0,
            "max": max_value or 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": "2%",
        },
        "series": [
            {
                "name": "党员数",
                "type": "heatmap",
                "data": heatmap_data,
                "label": {"show": True},
                "emphasis": {"itemStyle": {"shadowBlur": 10, "shadowColor": "rgba(0, 0, 0, 0.5)"}},
            }
        ],
    }


def _match_pie_spec(rows: list[dict[str, Any]]) -> dict[str, str] | None:
    if not rows:
        return None
    keys = set(rows[0].keys())
    for spec in _PIE_SPECS:
        if spec["label_key"] in keys and spec["value_key"] in keys:
            return spec
    return None


_VALUE_KEYS = frozenset({"total", "count"})
_LABEL_KEY_PRIORITY = (
    "age_group",
    "sexual",
    "sexual_value",
    "channel_value",
    "status_value",
    "merchant_name",
    "refund_date",
    "political_status_value",
    "party_branch_label",
    "marital_status",
    "grid_name",
    "building_name",
)


def _infer_distribution_spec(
    rows: list[dict[str, Any]],
    slots: dict[str, Any],
    intent: str | None,
    question: str | None,
) -> tuple[str, str, str] | None:
    if not rows:
        return None
    keys = list(rows[0].keys())
    value_key = next((key for key in keys if key in _VALUE_KEYS), None)
    if value_key is None:
        return None
    label_key = next((key for key in _LABEL_KEY_PRIORITY if key in keys), None)
    if label_key is None:
        candidates = [key for key in keys if key != value_key]
        if len(candidates) != 1:
            return None
        label_key = candidates[0]
    title = _distribution_title(label_key, slots, intent, question)
    return label_key, value_key, title


def _distribution_title(
    label_key: str,
    slots: dict[str, Any],
    intent: str | None,
    question: str | None,
) -> str:
    if label_key == "channel_value":
        return "支付渠道分布"
    if label_key == "status_value":
        return "支付订单状态分布"
    if label_key == "merchant_name":
        return "商户交易排名"
    return "分布统计"
