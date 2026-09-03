# Chart engine

`text2sql_runtime/visualization.py` builds ECharts options for query results.
It exposes two entry points:

- `maybe_build_chart(intent, rows, slots, question)` — for the whitelisted
  chart intents in `CHART_INTENTS` (payment channel / status distribution,
  refund daily trend, merchant rank).
- `maybe_build_generic_distribution_chart(question, rows, slots, intent)` —
  a schema-agnostic path used when the question mentions a chart keyword and
  the rows contain a value column (`total` or `count`) plus a dimension.

The rendered option is a plain dict that a browser can pass to `echarts.init(...).setOption(option)`.

## Keyword detection rule

`detect_requested_chart_type(question)` scans the question for keywords listed
in `CHART_TYPE_KEYWORDS`. Two rules, both testable:

1. **Longest keyword wins.** `"柱状趋势图"` contains `"柱状"` (2, bar) and
   `"趋势图"` (3, line) — line wins.
2. **On a length tie, the chart type listed earlier in `CHART_TYPE_KEYWORDS`
   wins.** The tuple orders specific types (`rose`, `donut`, `sankey`, ...)
   before generic ones (`bar`, `pie`), so genre-specific keywords beat
   generic ones when both match the same span.

If nothing matches and an intent has an entry in `INTENT_DEFAULT_CHART`,
that default is used.

## Type × capability matrix

Legend: **✓** = renders natively, **fallback→X** = intent's builder swaps to
type X because the requested type doesn't fit the data shape, **series** =
value of `series[0].type` on the produced ECharts option.

| Type              | Keywords                                                   | Generic path | payment_channel_* / payment_status_stat | refund_daily_trend | merchant_payment_rank | `series[0].type` | Notes                                     |
|-------------------|------------------------------------------------------------|--------------|-----------------------------------------|--------------------|-----------------------|------------------|-------------------------------------------|
| pie               | 饼图 / 圆饼图 / 占比图 / 扇形图 / 饼状图                    | ✓            | ✓ (default)                              | fallback→line      | fallback→bar          | pie              | default for distribution intents           |
| donut             | 甜甜圈图 / 环形图 / 圆环图                                   | ✓            | ✓                                        | fallback→line      | fallback→bar          | pie              | pie with inner radius                      |
| rose              | 南丁格尔玫瑰图 / 玫瑰图                                      | ✓            | ✓                                        | fallback→line      | fallback→bar          | pie              | `roseType: area`                           |
| bar               | 柱状图 / 柱形图 / 直方图 / 柱图                              | ✓            | ✓                                        | ✓                  | ✓ (default)           | bar              |                                            |
| horizontal_bar    | 条形图 / 条状图                                              | ✓            | ✓                                        | ✓                  | ✓                     | bar              | categories on yAxis                        |
| line              | 折线图 / 趋势图 / 走势图 / 曲线图 / 时序图 / 折线 / 趋势 / 走势 | ✓            | ✓                                        | ✓ (default)         | ✓                     | line             |                                            |
| area              | 面积图                                                        | ✓            | ✓                                        | ✓                  | fallback→bar          | line             | line with `areaStyle`                      |
| scatter           | 散点图                                                        | ✓            | ✓                                        | fallback→line      | fallback→bar          | scatter          |                                            |
| bubble            | 气泡图                                                        | ✓            | ✓                                        | fallback→line      | fallback→bar          | scatter          | all-zero values degrade to plain scatter    |
| radar             | 雷达图                                                        | ✓            | ✓                                        | fallback→line      | fallback→bar          | radar            | indicator max clamped ≥1                    |
| heatmap           | 热力图 / 热图 / heatmap                                       | ✓            | ✓                                        | fallback→line      | fallback→bar          | heatmap          | 1D strip (single row)                       |
| funnel            | 漏斗图                                                        | ✓            | ✓                                        | fallback→line      | fallback→bar          | funnel           | descending sort                             |
| waterfall         | 瀑布图                                                        | ✓            | ✓                                        | ✓                  | fallback→bar          | bar              | stacked bar; no delta computation           |
| boxplot           | 箱线图 / 盒须图                                                | ✓            | ✓                                        | fallback→line      | fallback→bar          | boxplot          | each point yields a degenerate box          |
| gantt             | 甘特图                                                        | ✓            | fallback→pie                             | fallback→line      | fallback→bar          | bar              | rendered as horizontal_bar                  |
| sankey            | 桑基图 / 桑基                                                 | ✓            | fallback→pie                             | fallback→line      | fallback→bar          | sankey           | title node → per-category links             |

## Data-shape rules

- **Empty rows** → `(None, None)` from both entry points. Nothing is rendered.
- **Missing / null label** — dropped, unless the builder passes an
  `empty_label` (channel/status use `"未填写"`, merchant uses `"未知商户"`).
- **Values** — passed through `_coerce_number`. `Decimal` and `float` retain
  their fractional part; only whole numbers narrow back to `int`. This
  matters for money aggregations (`SUM(amount)` → `Decimal`) — earlier the
  code truncated to `int` and silently dropped fractions.
- **Single row** — every builder still produces a valid option.
- **All zero values** — radar clamps indicator max to 1; bubble falls back
  to a plain scatter chart (no divide-by-zero on `symbolSize`).
- **Generic path** — requires a value column named `total` or `count` and
  either a known dimension (`channel_value`, `status_value`, `merchant_name`,
  `refund_date`) or exactly one non-value column.

## Adding a new chart type

1. Add the literal to `ChartType`.
2. Add its keyword tuple to `CHART_TYPE_KEYWORDS` — put it earlier in the
   tuple than any generic type it should beat on a length tie.
3. Add its human label to `CHART_TYPE_LABELS`.
4. Handle it in `_render_distribution_chart` (or extend an existing branch
   if it maps to the same series type as another).
5. Decide which intent builders should whitelist it in their `effective_type`
   set. Non-whitelisted types fall back to the builder's default (pie /
   line / bar).
6. Add row-fixture tests to `tests/test_visualization.py` covering
   detection, `series[0].type`, and any edge cases.
