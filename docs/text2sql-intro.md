# Text2SQL 入门：用支付场景讲清原理，以及改哪里生效

> 本文配套开源项目 **[text2sql-mvp](https://github.com/xiexiaojing/text2sql-mvp)**。  
> 用支付/交易 Demo 说明 Text2SQL 原理与开发方式，不涉及具体公司业务，也不绑定任何 Agent 平台。

**开源地址：** https://github.com/xiexiaojing/text2sql-mvp

**本地体验：**

```bash
git clone https://github.com/xiexiaojing/text2sql-mvp.git
cd text2sql-mvp
./start.sh
# 浏览器打开 http://127.0.0.1:8777/chat
```

---

## 1. Text2SQL 在干什么？

用户问：

> 各支付渠道交易金额分布，用饼图展示

系统要做三件事：

1. **听懂** — 按渠道分组、汇总金额、饼图展示
2. **查库** — 生成并执行一条安全的 `SELECT`
3. **展示** — 返回文字说明 + 表格/图表

常见 Demo 做法：把整库表结构丢给大模型，让它直接写 SQL。能跑通，但上线容易出问题：

- 查错租户（看到别的商户数据）
- 扫全表、性能失控
- 统计口径不一致
- 手机号等敏感字段泄露

更稳妥的做法：

> **先匹配业务口径 → 再生成 SQL → 最后过安全检查**

---

## 2. 一条完整链路（两轮对话）

### 第一轮

**用户：**「各支付渠道交易金额分布饼图」

**内部流程：**

```text
问题
 → 命中 intent：payment_channel_amount_distribution
 → 套用 SQL 模板
 → 注入 tenant_id（domainId → tenant_id 过滤）
 → SQL 护栏（仅 SELECT、白名单表/字段）
 → 执行查询
 → 生成 ECharts 饼图配置
```

**用户看到：** 渠道占比说明 + 饼图。

### 第二轮（追问）

**用户：**「折线图也生成一下」

单独看这句话，系统不知道「什么数据的折线图」。  
框架会结合上一轮对话，改写成：

> 各支付渠道交易金额分布折线图

然后再走同一套 intent → SQL → 出图流程。

**要点：** 多轮能力来自 `conversation.py` 里的追问改写，不是「模型记忆力好」。

---

## 3. 架构：三层就够

```text
┌─────────────────────────┐
│  API / MCP 入口          │  接收：question + domainId + history
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│  text2sql_runtime        │  理解 → 生成 SQL → 校验 → 查询 → 格式化/图表
└───────────┬─────────────┘
            │
┌───────────▼─────────────┐
│  configs/*.yaml          │  业务口径、白名单、性能阈值（主战场）
└─────────────────────────┘
```

记住：

> **业务怎么查，主要写在 YAML 里；代码负责安全地执行。**

仓库目录速览：

| 路径 | 说明 |
|------|------|
| `configs/business_semantics.yaml` | 意图 + SQL 模板 |
| `configs/whitelist_tables.yaml` | 可查询的表/字段/JOIN |
| `configs/performance.yaml` | 超时、扫描行数、允许函数 |
| `packages/text2sql_runtime/` | 核心运行时 |
| `eval_cases/cases.yaml` | 回归评测用例 |

---

## 4. 开发指南：想支持新问题，改哪里？

### 4.1 已有类似统计，只是问法不同

**例子：** 已支持「支付订单按渠道统计」，用户问「微信支付宝各占多少」。

**改：** `configs/business_semantics.yaml` 里对应 intent 的 `match.any` / `examples`：

```yaml
- id: payment_channel_stat
  match: {all: ["订单"], any: ["按渠道", "渠道统计", "微信", "支付宝", "银联"]}
  examples:
    - 支付订单按渠道统计
    - 微信支付宝银联各占多少
```

**通常不用写新 SQL。**

---

### 4.2 全新统计口径（最常见）

**例子：**「近 7 天每日退款笔数趋势」

**步骤 1 — 新增 intent**（`business_semantics.yaml`）：

```yaml
- id: refund_daily_trend
  display_name: 每日退款笔数趋势
  status: executable
  match: {all: ["退款"], any: ["趋势", "折线图", "近7天", "每日"]}
  template: refund_daily_trend
  examples:
    - 近7天每日退款笔数趋势
```

**步骤 2 — 写 SQL 模板**（同文件 `sql_templates` 区）：

```yaml
refund_daily_trend:
  plan: 近 7 天按日统计退款笔数
  sql: >
    SELECT DATE(ro.refund_time) AS refund_date, COUNT(*) AS total
    FROM refund_order ro
    WHERE ro.refund_time >= DATE_SUB(CURRENT_DATE, INTERVAL 7 DAY)
    GROUP BY refund_date
    ORDER BY refund_date
```

> 模板里**不必手写** `tenant_id = ...`，运行时会对白名单表自动注入 `domainId` 过滤。

**步骤 3 —** 确认 `refund_order` 在 `whitelist_tables.yaml`

**步骤 4 —** 在 `eval_cases/cases.yaml` 加测试问句

**步骤 5 —** 跑 `pytest` 和 `python scripts/run_evals.py`

---

### 4.3 换图表类型（饼图 → 折线图）

| 现象 | 改哪里 |
|------|--------|
| 「折线图也生成一下」被理解错 | `conversation.py`（或前端发送前改写） |
| 改写对了但不出图 | `visualization.py` + intent 是否在 `CHART_INTENTS` |
| 根本匹配不到 intent | 回到 4.2，补 `match` / `examples` |

---

### 4.4 返回「未配置该统计口径」

说明问题**没有命中任何 executable intent**。

排查顺序：

1. `business_semantics.yaml` 里有没有对应 intent？
2. `match.all` / `match.any` 是否过严？
3. `examples` / `semantic.queries` 是否覆盖用户问法？
4. `status` 是否为 `executable`？（`needs_mapping` 不会执行）

---

## 5. 三个配置文件

| 文件 | 作用 |
|------|------|
| `business_semantics.yaml` | 什么问题 → 哪个 intent → 哪条 SQL |
| `whitelist_tables.yaml` | 允许哪些表、字段、JOIN；`domain_column` 指定租户字段 |
| `performance.yaml` | 超时、EXPLAIN 行数上限、允许的 SQL 函数 |

**日常开发约 90% 时间在第一个文件。**

---

## 6. 四条安全原则

1. **租户 ID 由后端注入** — API 传入 `domainId`，运行时写入 SQL，不让模型自选范围
2. **只允许 SELECT** — 写操作一律拒绝
3. **只能查白名单表/字段** — 敏感列默认不可见
4. **无口径就拒绝** — 比「随便查一张表凑答案」更安全

---

## 7. 开发检查清单

新增一个支付类问答能力时：

- [ ] 一句话定义口径（表、分组维度、指标）
- [ ] `business_semantics.yaml` 增加 intent + match + examples
- [ ] 编写 `sql_templates` 条目
- [ ] `whitelist_tables.yaml` 声明表与 JOIN
- [ ] `eval_cases/cases.yaml` 加 2～3 条典型问法
- [ ] `pytest` 通过
- [ ] 需要图表时，问题里包含「饼图/柱状图/折线图」等词
- [ ] 需要多轮时，测试「那按状态呢」「折线图也生成一下」

---

## 8. 和「直接让 AI 写 SQL」对比

| | 直接 AI 写 SQL | text2sql-mvp |
|--|----------------|--------------|
| 口径一致性 | 不稳定 | 模板 + intent 保证 |
| 租户隔离 | 容易漏 | 后端强制注入 |
| 新需求 | 改 prompt 碰运气 | 改 YAML，可测试 |
| 排错 | 难 | 审计日志：intent、SQL、耗时 |
| 适用 | Demo | 要上线的业务系统 |

---

## 9. 结语

Text2SQL 不是「教会 AI 写 SQL」，而是：

> **把常见业务问题，映射成少量、可审核、可测试的查询模板。**

从一个场景跑通（例如「各支付渠道交易金额分布」），再按同样模式扩展，比一开始啃完整架构更容易上手。

**项目地址：** https://github.com/xiexiaojing/text2sql-mvp  
**架构细节：** [architecture.md](./architecture.md)  
**贡献指南：** [CONTRIBUTING.md](../CONTRIBUTING.md)
