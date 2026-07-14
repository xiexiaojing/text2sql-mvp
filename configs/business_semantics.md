# business_semantics.yaml 完整参数文档

> 本文档全面覆盖 `business_semantics.yaml` 中的**所有**可配置参数，包括顶层结构、意图定义、SQL 模板、动态实体查询架构、以及 Slot 槽位参数体系。

---

## 一、文件顶层结构

```yaml
version: 1                    # 配置文件版本号
notes:                        # 配置说明备注列表（可选）
  - 描述说明1
  - 描述说明2
entities:                     # 实体定义（实体→物理表映射）
  EntityName:
    tables: [table1, table2]
intents:                      # 意图列表（核心）
  - id: xxx
    ...                       # 参见第二节
sql_templates:                # SQL 模板定义
  template_name:
    ...                       # 参见第三节
entity_query_schemas:         # 动态实体查询架构定义（可选）
  entity_id:
    ...                       # 参见第四节
```

---

## 二、`intents` — 意图定义完整参数

### 2.1 基础标识参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `id` | `string` | **是** | 意图唯一标识，如 `user_count` |
| `display_name` | `string` | 否 | 意图显示名称，如 `用户总数`。不填则取 `id` 值 |
| `status` | `string` | 否 | 意图状态。可选值见下方。默认 `needs_mapping` |
| `priority` | `int` | 否 | 匹配优先级（0~100），同类问题更具体的意图设置更高值，默认 0 |

**`status` 可选值：**

| 值 | 含义 |
|----|------|
| `executable` | **可执行**：意图+模板+槽位全部就绪，能直接生成 SQL |
| `metadata` | **元数据路径**：仅用于字段解释等元信息查询 |
| `needs_mapping` | **缺少映射**：意图已识别但缺 SQL 模板（触发拒绝） |
| `guarded_text2sql` | **受控 LLM 生成**：允许使用 LLM 生成 SQL（不走模板） |
| `needs_clarification` | **需要澄清**：缺少 `required_slots` 导致，系统会反问问句 |

> 只有 `status=executable` 且配置了 `template` 的意图才会生成 SQL。

---

### 2.2 `match` — 关键词匹配规则

```yaml
match:
  all: ["关键词A", "关键词B"]     # AND 逻辑：必须全部出现
  any: ["关键词1", "关键词2"]     # OR 逻辑：每命中一个 +1 分
  none: ["排除词1", "排除词2"]    # NOT 逻辑：任一命中则直接跳过该意图
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `match.all` | `list[string]` | **必须全部命中**。任一未命中则跳过该意图。如 `["部门"]` |
| `match.any` | `list[string]` | **每命中一个 +1 分**。是评分核心。如 `["总数", "有多少", "数量"]` |
| `match.none` | `list[string]` | **排除词**：任一命中直接跳过整个 intent。用于防止意图被误匹配。如 `["用户", "男女"]` |

**评分公式：**
```
score = priority + hits(match.any) + example_hits × 2
confidence = min(0.99, max(0.5, score / 100))
```

**关键边界：** `distance ≤ 0.05`（即 `score ≥ 95`）时触发强词覆盖纠偏。

**调参建议：**
- `priority` 基线 80~95，每次 ±2~3
- `match_any` 达到 `score ≥ 95` 需要足够的命中数：`priority + hits ≥ 95`
  例：`priority=88` 需要 `hits≥7`，建议给关键意图多配同义关键词
- `match_none` 用于排他，避免"各部门人数"被 `dept_count` 抢走

---

### 2.3 `examples` — 示例问句

```yaml
examples:
  - "用户人数"
  - "集团有多少人"
  - "销售部有多少人"
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `examples` | `list[string]` | 精确**子串匹配**示例，每个命中 **+2 分**（权重是 `match_any` 的 2 倍）。同时作为快速路径跳过 LLM 的条件之一（问题与 example 完全匹配或 example 是问题的子串即跳过） |

---

### 2.4 `semantic` — 语义向量搜索配置

```yaml
semantic:
  queries:                      # 正面语义查询变体
    - "统计用户总数"
    - "用户一共有多少"
  negative_queries:             # 负例查询（用于向量区分）
    - "查询订单金额"
  boundary_queries:             # 边界查询（用于边界定义）
    - "边界用例1"
  boundary_negative_queries:    # 边界负例查询
    - "边界负例1"
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `semantic.queries` | `list[string]` | 语义向量搜索的**查询变体**。仅在向量索引启用时生效，不影响关键词评分 |
| `semantic.negative_queries` | `list[string]` | **负例查询**：帮助向量模型区分不应匹配到此意图的问法 |
| `semantic.boundary_queries` | `list[string]` | **边界查询**：定义意图匹配的边界用例 |
| `semantic.boundary_negative_queries` | `list[string]` | **边界负例查询**：边界用例的负例 |

---

### 2.5 `ontology_refs` & `physical_tables` — 实体与物理表关联

```yaml
ontology_refs: [User, Dept]          # 关联的实体名称（引用 entities 段的 key）
physical_tables: [sys_user, sys_dept] # 涉及的实际物理表名
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `ontology_refs` | `list[string]` | 关联的**实体**（对应 `entities` 段的 key）。用于语义理解上下文 |
| `physical_tables` | `list[string]` | 涉及的**物理表名**列表。用于 SQL Guard 白名单校验、表推断等 |

---

### 2.6 `Slots` 槽位体系 — `required_slots`、`optional_slots`、`slot_defaults`

```yaml
required_slots: [parent_dept_name]    # 必需槽位：必须先提取到才能执行
optional_slots: [dept_name]           # 可选槽位：有则增强、无则忽略
slot_defaults:                        # 槽位默认值
  dept_lvl: 2
  result_limit: 10
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `required_slots` | `list[string]` | **必需槽位**：必须从问句中提取到方可执行。未填充 → status 变为 `needs_clarification`。通常结合 LLM 提取使用 |
| `optional_slots` | `list[string]` | **可选槽位**：有则附加过滤条件、无则忽略该维度。例如 `dept_name` 可选时，"用户人数"统计全量、"集团人数"过滤部门 |
| `slot_defaults` | `dict` | **槽位默认值**：LLM/启发式提取失败时的降级值。例如 `dept_lvl: 2`、`result_limit: 10` |

---

### 2.7 `output_type` — 输出类型

```yaml
output_type: scalar_count    # 或 grouped_count / grouped_metric
```

| 值 | 含义 | 行为 |
|----|------|------|
| `scalar_count` | 标量计数 | 返回单个数字，**不展示表格**。如"共 128 人" |
| `grouped_count` | 分组计数 | 返回多行分组数据，**展示表格**。如按部门分组的人数列表 |
| `grouped_metric` | 分组指标聚合 | 返回分组的聚合指标数据，展示表格。如按供应商排名的采购单数 |
| 自定义值 | 自定义 | 可设为任意字符串，前端根据 `output_type` 决定展示逻辑 |

> `scalar_count` 特殊行为：当只有 1 行结果时，`_should_omit_scalar_table` 会隐藏数据表格，只显示文字答案。

---

### 2.8 `template` — SQL 模板引用

```yaml
template: user_count            # 指向 sql_templates 中的 key
# 或
template: dynamic_entity_query  # 特殊值：走动态实体查询路径
```

| 值 | 说明 |
|----|------|
| `{template_name}` | 指向 `sql_templates` 段中同名模板。模板中可使用 `%(slot)s` 参数占位、`[[slot:body]]` 可选块、`{{computed}}` 计算值 |
| `dynamic_entity_query` | **特殊值**：不走 SQL 模板，而是由 `EntityQueryCompiler` 根据 `entity_query_schemas` 动态生成 SQL |

---

### 2.9 `reason` & `needs` — 辅助参数

```yaml
reason: "该意图缺少可执行 SQL 模板"    # 拒绝原因
needs: ["entity_query"]                # 依赖需求声明
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `reason` | `string` | **拒绝原因**。当 `status=needs_mapping` 时向用户展示的解释文案 |
| `needs` | `list[string]` | **依赖需求**。声明意图依赖的能力。当前支持 `["entity_query"]`（表示走动态实体查询路径） |

---

## 三、`sql_templates` — SQL 模板定义

### 3.1 模板结构

```yaml
sql_templates:
  user_count:                          # 模板名（与 intents.template 对应）
    plan: 统计未删除的用户总数           # SQL 计划的文字描述（用于日志/审计）
    params:                             # 参数→槽位映射
      dept_name: dept_name              # 模板中的 %(dept_name)s 从 slots.dept_name 取值
    sql: >                              # SQL 模板字符串（支持三种特殊语法）
      SELECT COUNT(*) AS total
      FROM sys_user u
      [[dept_name:JOIN sys_dept d ON u.dept_id = d.dept_id]]
      WHERE u.del_flag = 0
      [[dept_name:AND d.del_flag = 0 AND d.dept_long_name like concat('%', %(dept_name)s, '%')]]
```

### 3.2 `plan` — 计划描述

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `plan` | `string` | 否 | SQL 计划的**自然语言描述**，会写入审计日志。不填则取模板名 |

### 3.3 `params` — 参数映射表

```yaml
params:
  dept_name: dept_name            # SQL 中的 %(dept_name)s ← slots.dept_name
  dept_lvl: dept_lvl              # SQL 中的 %(dept_lvl)s ← slots.dept_lvl
  parent_dept_name: parent_dept_name
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `params` | `dict[str→str]` | 定义 SQL 中 `%(key)s` 占位符与 slot 槽位的**映射关系**。格式：`param_name: slot_name`。只有 slot 值非空时才会注入参数 |

### 3.4 `sql` — SQL 模板字符串（支持三种特殊语法）

#### 3.4.1 `%(param_name)s` — 参数占位符

```sql
WHERE d.dept_lvl = %(dept_lvl)s
```

从 `params` 映射的 slot 值中取值，自动参数化防止注入。

#### 3.4.2 `[[slot_name: body]]` — 可选块语法

```sql
[[dept_name:JOIN sys_dept d ON u.dept_id = d.dept_id]]
[[dept_name:AND d.del_flag = 0 AND d.dept_long_name like concat('%', %(dept_name)s, '%')]]
```

| 语法 | 说明 |
|------|------|
| `[[slot:body]]` | **可选块**：当 `slot` 值**非空**时保留 `body` 文本；当 `slot` 值**为空**时整段移除 |

**典型用途：** 同一模板同时支持"全局统计"和"按维度过滤"两种问法。例如 `user_count` 模板中 `[[dept_name:...]]` 块：问"用户人数"时不带部门过滤，问"集团人数"时自动附加 JOIN + WHERE 条件。

#### 3.4.3 `{{computed_name}}` — 计算值占位符

```sql
WHERE u.born_at <= {{age_cutoff_ms}}
  AND u.created_at >= {{month_start_ms}}
```

| 语法 | 说明 |
|------|------|
| `{{computed}}` | **计算值占位符**：由系统运行时自动计算注入。下方为全部可用计算值 |

**全部可用计算值：**

| 计算值 | 类型 | 说明 |
|--------|------|------|
| `age_cutoff_ms` | int | 以 age slot（默认 60）计算出生的毫秒时间戳截断值 |
| `age_cutoff_18_ms` | int | 18 岁出生截断时间戳 |
| `age_cutoff_35_ms` | int | 35 岁出生截断时间戳 |
| `age_cutoff_60_ms` | int | 60 岁出生截断时间戳 |
| `month_start_ms` | int | 当月 1 日 00:00 的毫秒时间戳 |
| `month_end_ms` | int | 当月最后一日 23:59 的毫秒时间戳 |
| `week_start_ms` | int | 本周一 00:00 的毫秒时间戳 |
| `week_end_ms` | int | 本周日 23:59 的毫秒时间戳 |
| `year_start_ms` | int | 今年 1 月 1 日 00:00 的毫秒时间戳 |
| `last_year_start_ms` | int | 去年 1 月 1 日 00:00 的毫秒时间戳 |
| `last_year_end_ms` | int | 去年 12 月 31 日 23:59 的毫秒时间戳 |
| `half_year_start_ms` | int | 半年前（183天前）的毫秒时间戳 |
| `senior_next_year_birth_start_ms` | int | 明年满80岁的出生起始时间戳 |
| `senior_next_year_birth_end_ms` | int | 明年满80岁的出生结束时间戳 |
| `result_limit` | int | 结果行数限制（默认 10） |

---

## 四、`entity_query_schemas` — 动态实体查询架构

### 4.1 结构总览

```yaml
entity_query_schemas:
  party_member:                         # 实体ID（与 intents.template=dynamic_entity_query 配合使用）
    table: party_member                 # 物理表名
    alias: pm                           # SQL 别名（默认取 entity_id 首字母）
    display_name: 党员                  # 实体显示名称
    attributes:                         # 实体属性定义
      sexual:                           # 属性名
        kind: enum                      # 属性类型
        column: sex                     # 对应数据库列名
        label: 性别                     # 属性标签（中文名）
        values:                         # 枚举值映射（仅 enum/boolean 类型）
          "男": ["男性", "男党员"]
          "女": ["女性", "女党员"]
        group_alias: sex_name           # 分组别名（SELECT AS 的名称）
      is_local:                         # 布尔属性
        kind: boolean
        column: is_local_resident
        label: 本地户籍
        values:
          "1": ["本地", "本地户籍"]
          "0": ["非本地", "外地", "外地户籍"]
      age:                              # 年龄过滤属性
        kind: age
        column: born_at                 # 出生时间戳列（用于计算年龄比较）
      age_group:                        # 年龄段分组属性
        kind: age_group
        column: born_at
        label: 年龄段
      party_branch_name:                # 标签分组属性
        kind: label_group
        column: party_branch_name
        label: 所在党支部
        group_alias: branch_name
```

### 4.2 Entity Schema 参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `table` | `string` | **是** | 物理表名 |
| `alias` | `string` | 否 | SQL 表别名，默认取 `entity_id` 首字母（如 `pm`） |
| `display_name` | `string` | 否 | 实体中文显示名，默认取 `entity_id` |
| `attributes` | `dict` | **是** | 属性定义字典 |

### 4.3 Attribute 属性参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `kind` | `string` | **是** | 属性类型：`enum`、`boolean`、`age`、`age_group`、`label_group` |
| `column` | `string` | 否 | 对应数据库列名（`age_group`、`label_group` 可用 `expression` 替代） |
| `label` | `string` | 否 | 属性中文标签，用于启发式分组关键词匹配 |
| `values` | `dict[string→list]` | 否 | 枚举值→别名列表映射（仅 `enum`/`boolean` 类型） |
| `expression` | `string` | 否 | SQL 表达式（仅 `age_group`/`label_group` 类型）。支持 `{alias}` 占位符替换为表别名 |
| `group_alias` | `string` | 否 | GROUP BY 时 SELECT 的别名 |

### 4.4 `kind` 五种类型详解

| kind | 用途 | 典型场景 | 必需字段 |
|------|------|----------|----------|
| `enum` | 枚举值过滤与分组 | 性别（男/女）、婚姻状态 | `column`, `values` |
| `boolean` | 布尔值过滤与分组 | 是否本地户籍、是否党员 | `column`, `values` |
| `age` | 年龄过滤（问句中的"X岁"） | 60岁以上、18岁以下 | `column` |
| `age_group` | 年龄段分组（不分枚举值） | 各年龄段分布（0-17/18-34/35-59/60+） | `column` 或 `expression` |
| `label_group` | 标签分组（用于 GROUP BY） | 按党支部、按楼栋分组统计 | `column` 或 `expression` |

### 4.5 entity_query_spec 运行时数据格式

当 LLM 提取或系统推断出动态实体查询意图后，会在 `slots` 中生成如下结构：

```json
{
  "entity": "party_member",
  "entity_query": {
    "entity": "party_member",
    "metric": "count",
    "filters": [
      {"field": "sexual", "op": "=", "value": "男"},
      {"field": "age", "op": ">=", "value": 60}
    ],
    "group_by": ["party_branch_name"],
    "order_by": [{"field": "total", "direction": "desc"}],
    "limit": 10
  }
}
```

| 子字段 | 类型 | 说明 |
|--------|------|------|
| `entity` | `string` | 实体ID |
| `metric` | `string` | 聚合指标。当前仅支持 `"count"` |
| `filters` | `list[object]` | 过滤条件列表。每个 `{field, op, value}` |
| `group_by` | `list[string]` | 分组字段列表（必须是 `enum`/`boolean`/`age_group`/`label_group` 类型属性） |
| `order_by` | `list[object]` | 排序规则。仅支持 `{"field": "total", "direction": "desc"/"asc"}` |
| `limit` | `int` | 返回行数上限 |

---

## 五、Slot 槽位参数完整参考

以下是系统内置支持的所有槽位名称、提取方式和使用说明。

### 5.1 时间相关槽位

| 槽位名 | 提取方式 | 说明 |
|--------|----------|------|
| `age` | 正则 `\d{2,3}\s*岁` | 年龄，默认 60 |
| `age_cutoff_ms` | 由 `age` 自动计算 | 年龄倒推的出生时间毫秒截断 |
| `month_start_ms` | 自动计算 | 当月起始时间戳 |
| `month_end_ms` | 自动计算 | 当月结束时间戳 |
| `week_start_ms` | 自动计算 | 本周起始时间戳 |
| `week_end_ms` | 自动计算 | 本周结束时间戳 |
| `year_start_ms` | 自动计算 | 今年起始时间戳 |
| `current_year` | 自动计算 | 当前年份（整数） |
| `current_month` | 自动计算 | 当前月份（整数） |
| `apply_month_scope` | 检测"本月/这个月/当月" | 是否应用本月范围过滤 |
| `apply_week_scope` | 检测"本周/这周/当周" | 是否应用本周范围过滤 |

### 5.2 人员属性槽位

| 槽位名 | 提取方式 | 说明 |
|--------|----------|------|
| `sexual` | 检测"男/女/男性/女性/男党员" 等 | 性别（返回 "男"/"女"） |
| `marital_status` | 检测"未婚/已婚/离异/离婚/丧偶" | 婚姻状态 |
| `person_name` | 正则+中文姓名识别 | 姓名（2-4字中文） |
| `person_name_like` | 由 `person_name` 自动推导 | 姓名模糊匹配值 `%name%` |
| `phone` | 正则 `1\d{10}` | 手机号码 |
| `card_no` | 正则身份证号格式 | 身份证号 |

### 5.3 部门/组织相关槽位

| 槽位名 | 提取方式 | 说明 |
|--------|----------|------|
| `dept_name` | 正则 "XX（有多少人/人数）" | 部门全称（用于模糊过滤） |
| `parent_dept_name` | 正则 "XX（下级/下属/子部门）" | 父级部门名 |
| `dept_lvl` | 正则 "各X级部门"（二~九） | 部门层级（整数，默认 2） |
| `party_branch_name` | 启发式+LLM | 党组织名称 |
| `party_branch_path_like` | 由 `party_branch_name` 推导 | 党组织路径模糊匹配 `%/branch/%` |

### 5.4 业务对象槽位

| 槽位名 | 提取方式 | 说明 |
|--------|----------|------|
| `merchant_name` | 正则 "XX（联系人/负责人）" | 商户/商家名称 |
| `merchant_name_like` | 由 `merchant_name` 推导 | 商户名模糊匹配值 |
| `area_name` | 启发式+LLM | 区域名称 |
| `area_like` | 由 `area_name` 推导 | 区域模糊匹配值 |
| `category` | 启发式+LLM | 分类名称 |
| `category_like` | 由 `category` 推导 | 分类模糊匹配值 |
| `skill` | 启发式+LLM | 技能名称 |
| `skill_like` | 由 `skill` 推导 | 技能模糊匹配值 |
| `grid_name` | 启发式+LLM | 网格名称 |
| `grid_name_like` | 由 `grid_name` 推导 | 网格模糊匹配值 |
| `field_name` | 正则 `table.column` 格式 | 字段全名（用于字段解释） |
| `table_name` | 由 `field_name` 解析 | 表名 |
| `column_name` | 由 `field_name` 解析 | 列名 |
| `field_like` | 由 `field_name` 推导 | 字段模糊匹配值 |
| `tag_name` | 启发式+LLM | 标签名称 |
| `tag_like` | 由 `tag_name` 推导 | 标签模糊匹配值 |
| `role` | 启发式+LLM | 角色名称 |
| `role_like` | 由 `role` 推导 | 角色模糊匹配值 |
| `address` | 地址解析器 | 地址文本 |
| `address_like` | 由 `address` 推导 | 地址模糊匹配值 |

### 5.5 通用控制槽位

| 槽位名 | 提取方式 | 说明 |
|--------|----------|------|
| `result_limit` | 正则 "TOP N" / "前 N" | 结果条数限制（默认 10） |

---

## 六、完整示例

以下是一个涵盖最多参数的完整示例，演示如何配置一个"按性别过滤、按部门分组、支持部门层级和父部门"的复杂意图：

```yaml
version: 1
notes:
  - 综合示例，展示所有参数用法
  - 实际项目中根据业务需求选择需要的参数即可

entities:
  User:
    tables: [sys_user]
  Dept:
    tables: [sys_dept]

intents:
  - id: user_sex_by_dept             # 意图唯一标识
    display_name: 用户性别按部门统计 # 显示名称
    status: executable               # 可执行状态
    priority: 85                     # 匹配优先级
    match:                           # 关键词匹配规则
      all: ["用户", "部门"]          # 必须同时包含"用户"和"部门"
      any:                          # 每命中一个 +1 分
        - "性别"
        - "男女"
        - "按部门"
        - "各部门"
        - "部门分布"
        - "部门统计"
      none:                         # 任一命中则跳过
        - "项目"
        - "采购"
    examples:                       # 每个子串匹配 +2 分
      - "各部门用户性别分布"
      - "按部门统计男女比例"
      - "部门用户男女各有多少"
    semantic:                       # 语义向量搜索配置
      queries:
        - "按部门统计男女人数"
        - "部门性别构成"
      negative_queries:
        - "查询订单金额"
      boundary_queries:
        - "部门男女人数"
      boundary_negative_queries:
        - "项目性别人数"
    ontology_refs: [User, Dept]     # 关联实体
    physical_tables:                # 涉及物理表
      - sys_user
      - sys_dept
    required_slots: [dept_name]     # 必须提取到部门名
    optional_slots:                 # 可选槽位
      - sexual
      - dept_lvl
      - result_limit
    slot_defaults:                  # 槽位默认值
      dept_lvl: 2
      result_limit: 20
      sexual: 男                    # 默认查男性
    output_type: grouped_count      # 输出类型
    template: user_sex_by_dept      # SQL 模板名
    reason: "该意图缺少可执行SQL模板" # 备用拒绝原因
    needs: []                       # 依赖需求

sql_templates:
  user_sex_by_dept:
    plan: 按部门统计用户性别分布
    params:
      dept_name: dept_name
      dept_lvl: dept_lvl
    sql: >
      SELECT d.dept_name AS dept_name,
             CASE u.sex WHEN 0 THEN '男' WHEN 1 THEN '女' ELSE '未知' END AS sex_name,
             COUNT(*) AS total
      FROM sys_user u
      JOIN sys_dept d ON u.dept_id = d.dept_id
      WHERE u.del_flag = 0
        AND d.del_flag = 0
        [[dept_name:AND d.dept_long_name like concat('%', %(dept_name)s, '%')]]
        [[dept_lvl:AND d.dept_lvl = %(dept_lvl)s]]
        [[sexual:AND u.sex = CASE %(sexual)s WHEN '男' THEN 0 WHEN '女' THEN 1 ELSE -1 END]]
        AND u.born_at <= {{age_cutoff_60_ms}}
      GROUP BY d.dept_name, u.sex
      ORDER BY total DESC
      LIMIT {{result_limit}}
```

---

## 七、图表支持

系统内置 16 种 ECharts 图表类型，可在聊天界面通过关键词切换图表类型。图表默认优先根据问题中的关键词自动检测，其次根据 `INTENT_DEFAULT_CHART` 映射。

### 7.1 支持图表类型及触发词

| 图表类型 | 中文名 | 触发关键词 |
|----------|--------|------------|
| `pie` | 饼图 | 饼图、圆饼图、占比图、扇形图、饼状图 |
| `donut` | 环形图 | 甜甜圈图、环形图、圆环图 |
| `rose` | 南丁格尔玫瑰图 | 南丁格尔玫瑰图、玫瑰图 |
| `bar` | 柱状图 | 柱状图、柱形图、直方图、柱图 |
| `horizontal_bar` | 条形图 | 条形图、条状图 |
| `line` | 折线图 | 折线图、趋势图、走势图、曲线图、时序图、折线、趋势、走势 |
| `area` | 面积图 | 面积图 |
| `scatter` | 散点图 | 散点图 |
| `bubble` | 气泡图 | 气泡图 |
| `radar` | 雷达图 | 雷达图 |
| `heatmap` | 热力图 | 热力图、热图、heatmap |
| `funnel` | 漏斗图 | 漏斗图 |
| `waterfall` | 瀑布图 | 瀑布图 |
| `boxplot` | 箱线图 | 箱线图、盒须图 |
| `gantt` | 甘特图 | 甘特图 |
| `sankey` | 桑基图 | 桑基图、桑基 |

### 7.2 意图默认图表映射

```yaml
# 代码中硬编码（如有需要可改为 YAML 配置）
payment_channel_amount_distribution → pie
refund_daily_trend                  → line
merchant_payment_rank               → bar
payment_channel_stat                → pie
payment_status_stat                 → bar
```

---

## 八、参数速查表

### 意图 (intent) 所有参数

| 参数 | 类型 | 必需 | 默认值 |
|------|------|------|--------|
| `id` | string | **是** | — |
| `display_name` | string | 否 | `id` 值 |
| `status` | string | 否 | `needs_mapping` |
| `priority` | int | 否 | `0` |
| `match.all` | list[string] | 否 | `[]` |
| `match.any` | list[string] | 否 | `[]` |
| `match.none` | list[string] | 否 | `[]` |
| `examples` | list[string] | 否 | `[]` |
| `semantic.queries` | list[string] | 否 | `[]` |
| `semantic.negative_queries` | list[string] | 否 | `[]` |
| `semantic.boundary_queries` | list[string] | 否 | `[]` |
| `semantic.boundary_negative_queries` | list[string] | 否 | `[]` |
| `ontology_refs` | list[string] | 否 | `[]` |
| `physical_tables` | list[string] | 否 | `[]` |
| `required_slots` | list[string] | 否 | `[]` |
| `optional_slots` | list[string] | 否 | `[]` |
| `slot_defaults` | dict | 否 | `{}` |
| `output_type` | string | 否 | — |
| `template` | string | 否 | — |
| `reason` | string | 否 | — |
| `needs` | list[string] | 否 | `[]` |

### SQL 模板 (sql_template) 所有参数

| 参数 | 类型 | 必需 | 默认值 |
|------|------|------|--------|
| `plan` | string | 否 | 模板名 |
| `params` | dict[str→str] | 否 | `{}` |
| `sql` | string | **是** | — |

### 实体查询架构 (entity_query_schema) 所有参数

| 参数 | 类型 | 必需 | 默认值 |
|------|------|------|--------|
| `table` | string | **是** | — |
| `alias` | string | 否 | entity_id 首字母 |
| `display_name` | string | 否 | entity_id |
| `attributes` | dict | **是** | — |

### 实体属性 (entity attribute) 所有参数

| 参数 | 类型 | 必需 | 默认值 |
|------|------|------|--------|
| `kind` | string | **是** | — |
| `column` | string | 否 | — |
| `label` | string | 否 | — |
| `values` | dict[string→list[string]] | 否 | `{}` |
| `expression` | string | 否 | — |
| `group_alias` | string | 否 | — |

---

> **本文档对应代码版本：** `business_semantics.py` (BusinessSemanticIndex.from_config), `entity_query.py` (EntityQueryCompiler.from_config), `semantic_slot_extractor.py` (LlmSlotExtractor), `visualization.py` (图表系统)
