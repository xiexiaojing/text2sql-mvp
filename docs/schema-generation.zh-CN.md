# 模式生成

从只读 MySQL 连接生成或刷新 `configs/whitelist_tables.yaml`。

## 从在线数据库内省

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

提交前请检查生成的文件：

- 移除不希望暴露的表/列
- 标记敏感列（`sensitive: true`、`searchable: false`）
- 添加 `display_name` 标签用于 UI 展示
- 显式声明安全的 `joins` 连接关系

## 业务语义

更新白名单后：

1. 在 `business_semantics.yaml` 中添加匹配的 `entities` 和 `physical_tables`
2. 为每个可执行意图创建 `sql_templates`
3. 在 `eval_cases/cases.yaml` 中添加评估用例

本 MVP 不包含自动意图生成 —— 模板需要人工审核，以确保 SQL 行为可预测。

## JPA / ORM 元数据（可选）

如果你的后端使用了 JPA 技术栈，`scripts/extract_jpa_metadata.py` 可以从 JPA 实体定义中提取表元数据。将其指向实体源码目录，然后手动将输出合并到白名单中。
