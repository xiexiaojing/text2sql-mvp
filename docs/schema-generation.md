# Schema generation

Generate or refresh `configs/whitelist_tables.yaml` from a read-only MySQL connection.

## Introspect live database

```bash
source .venv/bin/activate
python scripts/introspect_schema.py \
  --host 127.0.0.1 \
  --port 3306 \
  --database text2sql_demo \
  --user readonly \
  --password secret \
  --tables payment_order,refund_order,merchant \
  --domain-column tenant_id \
  --output configs/whitelist_tables.yaml
```

Review the generated file before committing:

- Remove tables/columns you do not want exposed
- Mark sensitive columns (`sensitive: true`, `searchable: false`)
- Add `display_name` labels for UI formatting
- Declare safe `joins` explicitly

## Business semantics

After updating the whitelist:

1. Add matching `entities` and `physical_tables` in `business_semantics.yaml`
2. Create `sql_templates` for each executable intent
3. Add eval cases in `eval_cases/cases.yaml`

There is no automatic intent generation in this MVP — templates are reviewed manually to keep SQL predictable.

## JPA / ORM metadata (optional)

`scripts/extract_jpa_metadata.py` can bootstrap table metadata from JPA entity definitions if your backend uses that stack. Point it at your entity source tree and merge the output into the whitelist by hand.
