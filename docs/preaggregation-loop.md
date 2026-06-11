# Pre-aggregation loop (optional)

When live queries repeatedly scan large fact tables, consider materialized summaries instead of widening SQL templates.

## Example pattern

```yaml
question_pattern: 商户交易金额排名
metric: merchant_payment_total
grain: merchant_id
source_tables: [merchant, payment_order]
refresh: daily
```

## Workflow

1. Capture slow or high-scan queries from the audit store (`/v1/audit/unsupported` and planned queries with high EXPLAIN rows).
2. Design a summary table that matches an existing intent grain (channel, merchant, day).
3. Point the intent template at the summary table while keeping the same natural-language surface.
4. Add eval cases to ensure semantic routing still hits the intent.

This MVP ships with direct table templates only; pre-aggregation is a deployment concern.
