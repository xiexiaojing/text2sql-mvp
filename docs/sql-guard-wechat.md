标题：那层拦 SQL 的墙，长什么样

副标题：AST 看 SQL 比正则靠谱

我写了二十年代码，在 text2sql-mvp 维护 SQL 护栏（`sql_guard.py`）。姑娘问：「LLM 写完 SQL 用正则扫关键字够吗？」三件事。

关键字正则不够。`LIKE '%delete%'` 会误伤，子查询里没禁词。护栏先给 sqlglot 出 AST：多语句甩 `multi_statement`，解析崩甩 `sql_parse_failed`，非 Select 甩 `not_select`；子查询靠 `\bfrom\s*\(` 甩 `subquery`。

表字段查白名单。`merchant JOIN payment_order` 是审过的对，换 `refund_order` 甩 `join_not_allowed`；`payer_mobile` 标 `sensitive: true` 甩 `sensitive_column`；`*_standard_history` 后缀甩 `history_table`。

租户隔离在护栏层做。模板、LLM、实习生都会忘 `tenant_id`。`_validate_domain_filter` 对每张表要 `alias.tenant_id=%(domain_id)s`，缺一张甩 `missing_domain_filter`。

demo 表上真跑过：

```sql
UPDATE payment_order SET status='x'  -- 拦 not_select
SELECT po.payer_mobile FROM payment_order po
WHERE po.tenant_id=%(domain_id)s LIMIT 10  -- 拦 sensitive_column
SELECT COUNT(*) FROM merchant m JOIN payment_order po ON po.merchant_id=m.id
WHERE m.tenant_id=%(domain_id)s AND po.tenant_id=%(domain_id)s  -- 放行 白名单 join
```

跑 `pytest tests/test_sql_guard.py -v`，每条拒答码都对得上。
