# 预聚合方案（可选）

当在线查询反复扫描大型事实表时，可考虑使用物化汇总表，而非不断扩展 SQL 模板。

## 模式示例

```yaml
question_pattern: 商户交易金额排名
metric: merchant_payment_total
grain: merchant_id
source_tables: [merchant, payment_order]
refresh: daily
```

## 工作流程

1. 从审计存储中捕获慢查询或高扫描量查询（`/v1/audit/unsupported` 和 EXPLAIN rows 较高的已计划查询）。
2. 设计与现有意图粒度（渠道、商户、日期）匹配的汇总表。
3. 将意图模板指向汇总表，同时保持相同的自然语言表达。
4. 添加评估用例，确保语义路由仍能命中该意图。

本 MVP 仅支持直接表模板；预聚合属于部署层面的考量。
