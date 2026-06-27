# Text2SQL 快速模式 · VIP 课讲课稿

> 配套项目：[text2sql-mvp](https://github.com/xiexiaojing/text2sql-mvp)（支付 Demo）  
> 社区场景参考：[community-text2sql-mvp](https://github.com/xiexiaojing/community-text2sql-mvp)  
> 架构图与术语表：`docs/architecture.md`

---

## 开场：这节课讲什么

这节课不讲「怎么调 Prompt 让模型写 SQL」，讲 **生产级问数助手** 的底层骨架：

1. **快速模式 Text2SQL** 和 **专家模式 Agent OS** 怎么分工
2. 一条请求从用户输入到图表输出的 **十步链路**
3. **对话改写规则引擎** 怎么设计、怎么 demo
4. **意图识别**：向量召回、关键词打分、confidence 门槛、防误命中
5. 规则怎么 **数据驱动迭代**（不是训练模型，是迭代配置）

核心一句话：

> **LLM 负责推理，Harness 负责现实。** 该用模型的地方用，不该用的地方用规则、模板、护栏。

---

## 一、两种架构：Text2SQL vs Agent OS

### 1.1 行业常见做法的问题

很多 Text2SQL 流水线长这样：

```text
用户问题
  → LLM 意图识别
  → LLM 改写增强
  → 向量库召回 Schema
  → LLM 可行性评估
  → LLM 语义检查
  → LLM 生成 SQL
  → 执行
```

每一步都「更智能一点」，串起来却有两个通病：**慢、不稳定**。一步错，后面再检查往往是在确认错误，不是纠正错误。

### 1.2 我们的分工

| | 快速模式（Text2SQL） | 专家模式（Agent OS） |
|--|---------------------|---------------------|
| **覆盖** | 80%+ 高频、口径固定的统计问数 | ~20% 探索、编排、研判、写报告 |
| **路径** | 召回 → 精排 → **模板** → 护栏 → 执行 | **规划 → 多步推理 → 多次调工具/模型** |
| **LLM 次数** | 最多 1 次（精排/兜底） | 允许多次 |
| **响应** | 目标 ~1 秒 | 分钟级可接受 |
| **输出** | 可枚举、可审核的 SQL + 图表 | 跨库查数 + 调 API + 汇总报告 |
| **核心假设** | 口径能模板化 | 任务不能预先写死 |

**互补，不是二选一。** 会话内一般不混用两种架构。

### 1.3 怎样才算 Agent OS

不是「接了大模型」或「能调工具」就叫 Agent OS。Agent OS 是给 Agent 提供 **运行时操作系统**：

| 能力 | 说明 |
|------|------|
| **Task loop** | 感知 → 规划 → 调工具 → 观察 → 再规划，多步循环 |
| **Tool registry** | 工具统一注册、鉴权、审计 |
| **Task memory** | 不只对话 history，还有任务级中间状态 |
| **Policy / Harness** | 步数预算、token 预算、工具白名单、敏感数据策略 |
| **Step-level audit** | 每一步可回放、可排错 |

**不算 Agent OS 的：**

- 端到端 LLM 写 SQL（没有 OS 层编排）
- 带 function calling 的单轮 Chatbot（缺规划循环和任务状态）
- text2sql-mvp **快速模式**（固定流水线，模板为主）

讲课用的一句话：

> **Agent OS = 让 Agent 在受控环境里自己规划、多步调工具、带记忆、可审计；Text2SQL 快速模式 = 在受控环境里走固定问数流水线。**

---

## 二、请求链路：从架构图到十步骨架

### 2.1 总览

```text
用户问题 → 对话改写 → 记忆检索 → 业务语义路由
                                    ├─ 可执行   → SQL 模板编译 ─────┐
                                    ├─ 受控兜底  → Schema/LLM 兜底 ─┤
                                    └─ 需映射   → 拒答
                                                                ↓
                                          租户注入 + LIMIT → SQL 护栏 AST → EXPLAIN + 执行
                                                                ↓
                                                   格式化 + 图表 → 审计日志
```

### 2.2 路由三分支

| 分支 | 配置 `status` | 说明 |
|------|---------------|------|
| 可执行 | `executable` | 命中意图且有模板，走 SQL 模板编译 |
| 受控兜底 | `guarded_text2sql` | 无模板，在 Schema 白名单内由 LLM 生成（框架保留，默认不主用） |
| 需映射 | `needs_mapping` | 口径未配置，拒答并返回原因 |

### 2.3 十步骨架（对照架构图讲）

快速模式不是「让模型写 SQL」，而是 **召回 → 精排 → 模板 → 护栏**。

| 步骤 | 做什么 | 是否调大模型 |
|------|--------|--------------|
| 1. 对话改写 | 「折线图也生成一下」→ 补全成完整问句（规则引擎） | 否 |
| 2. 记忆检索 | 加载用户/租户确认过的口径、映射、纠正规则 | 否 |
| 3. 意图识别 | 向量召回 → 关键词打分 → 可选 LLM 精排 + 抽槽 | 最多 1 次 |
| 4. 路由决策 | 可执行走模板 / 受控兜底 / 需映射拒答 | 视路径 |
| 5. SQL 生成 | 模板渲染，或 Schema 白名单内受控生成 | 兜底路径可能 1 次 |
| 6. 租户注入 | 后端强制加 `tenant_id`，不让模型决定查哪个组织 | 否 |
| 7. SQL 护栏 | 白名单表字段、只读、行数 LIMIT | 否 |
| 8. EXPLAIN 预检 | 估算扫描行数，过重则拒执行 | 否 |
| 9. 执行 + 格式化 | 查库 → 文字摘要 + 表格 + ECharts 图表 | 否 |
| 10. 审计 | 记录问了什么、命中哪个意图、用了哪条 SQL | 否 |

### 2.4 术语对照（一带而过）

| 中文 | 英文 | 含义 |
|------|------|------|
| 召回 | Retrieve | 从大量意图里先捞出候选 |
| 精排 | Rank | 在候选里选出最终意图 |
| 模板 | Template | 预先审核的 SQL 骨架 |
| 护栏 | Harness | 规则、校验、权限边界 |
| 可执行 | `executable` | 有模板，走主路径 |
| 受控兜底 | `guarded_text2sql` | 无模板，白名单内生成 |
| 需映射 | `needs_mapping` | 口径未配，拒答 |

---

## 三、基础概念（课前对齐）

### 3.1 召回 vs 精排

- **召回**：从 10 万本书里先找出 5～10 本可能相关的——缩小范围。
- **精排**：从中挑出最匹配的一本——最终决策。

召回 ≠ 最终答案。

### 3.2 向量与 Embedding

- **向量**：文本映射成一串数字；语义相近，距离更近。
- **Embedding**：承担「文本 → 向量」的模型或过程。

Embedding **只负责把文字变成可比较的坐标，不负责写 SQL**。

### 3.3 RAG 与 Text2SQL 的关系

RAG 完整流程：

```text
用户问题 → 检索相关资料 → 拼进 Prompt → LLM 自由生成
```

Text2SQL 快速模式 **只借用 RAG 前半段「检索」**，后半段走 **预定义 SQL 模板**，不交给 LLM 自由写 SQL。

### 3.4 意图与槽位

- **意图（Intent）**：用户想干什么——「查支付渠道金额分布」vs「查退款趋势」。
- **槽位（Slot）**：意图里要填的参数——`近7天`、`退款`、`按渠道` 等。

意图识别 = 判断哪种业务问题；槽位抽取 = 从问句里抠参数。

### 3.5 模板

预先写好、审核过的 SQL 骨架，运行时填槽位。用户问法千变万化，**SQL 逻辑固定**——口径一致、可测试、可审计。

支付 Demo 里 **8 个意图** 就覆盖绝大部分高频问数；99% 查询重复性极高，不必为泛化而泛化。

---

## 四、对话改写与规则引擎

### 4.1 为什么需要对话改写

多轮追问往往不完整：

```text
第一轮：「各支付渠道交易金额分布饼图」
第二轮：「折线图也生成一下」
```

若直接把第二句当独立问句，意图识别会失败或误命中。需要结合 **对话历史**，改写成完整问句：

```text
「各支付渠道交易金额分布折线图」
```

**改写统一在后端**（`text2sql-mvp` / `community-text2sql-mvp`），前端只传 `question` + `history`。

### 4.2 什么算「规则引擎」

**规则引擎** = 把业务逻辑从流程代码里拆出来，变成 **可独立注册、按策略执行** 的规则集合，由 **统一调度器** 运行。

四要素：

| 要素 | 含义 | 本项目实现 |
|------|------|------------|
| 规则抽象 | 每条规则：id + 条件 + 动作 | `FollowUpRule(id, priority, rewrite)` |
| 规则注册 | 集中管理，不散落 if/elif | `_build_rules()` → `_RULES` |
| 调度策略 | 谁先跑、跑几个、冲突怎么办 | 按 `priority` 降序，**第一个成功即停** |
| 输入输出分离 | 引擎不管下游 SQL | 入 `(question, history)`，出 `effective_question` + `reason` |

代码位置：

- 入口：`conversation.py` → `contextualize_question()`
- 调度器：`conversation_rewrite.py` → `apply_follow_up_rewrites()`
- 共享工具：`conversation_context.py`

### 4.3 成熟度光谱

```text
if/elif 链
    ↓
轻量规则引擎（text2sql-mvp）   ← 函数注册，代码里配 priority
    ↓
配置驱动规则引擎               ← yaml 定义 trigger + transform
    ↓
完整规则引擎（Drools、RETE）    ← 大量规则互相触发
```

对话改写约 10 条规则、变化不频繁，**轻量规则引擎足够**。

### 4.4 规则示例（text2sql-mvp）

| priority | 规则 id | 类型 |
|---------|---------|------|
| 100 | `chart_type_follow_up` | 通用：饼图 → 折线图 |
| 91 | `count_to_list_follow_up` | 通用：「有多少人」→「是谁」→「有哪些」 |
| 10 | `dimension_slot_follow_up` | 通用：「那按状态呢」→ 替换上轮分组维度 |
| 95–89 | 领域专用 | person_count / grid / ledger … |

**维度槽位规则** `dimension_slot_follow_up`：用户只说新维度（「那按状态呢」），引擎从上轮问句里保留主题，只替换 GROUP BY 维度——这是 **规则引擎 + 槽位** 的配合，不是 LLM 记忆。

## 五、意图识别：向量 + 关键词 + LLM

### 5.1 三阶段

```text
用户问句
    │
    ├─ 向量召回：semantic.queries / examples → Embedding → Top-K 候选
    │     └─ negative_queries / boundary_queries 防误召回
    │
    ├─ 关键词打分：match.all / match.any / examples → _best_intent()
    │     └─ 合并为 lexical 候选（distance = 1 - confidence）
    │
    └─ 可选 LLM 精排 + 抽槽（fast path 能跳过则跳过）
```

### 5.2 向量召回配置（business_semantics.yaml）

每个意图可配：

| 字段 | 作用 |
|------|------|
| `semantic.queries` | 正样本，代表这个意图的典型问法 |
| `semantic.negative_queries` | 语义像但不是本意图 → 太近则剔除 |
| `semantic.boundary_queries` | 意图边界样本 |
| `semantic.boundary_negative_queries` | 与易混意图交界处的负样本 |

向量索引内部用 **negative_margin**（≈ `distance_threshold × 0.25`）：正样本 distance 若不比 negative 明显更近，该意图不进候选。

### 5.3 关键词打分 `_best_intent()`

```python
score = priority + hits + (example_hits × 2)
# hits = match.any 命中数
# example_hits = examples 整句包含命中数
# 取 max(score) 的意图为赢家
```

**选意图看 score，不看 confidence。**

社区版额外门槛（支付 Demo 尚未接入）：

```python
# 无 example 命中时，match.any 至少命中 min_lexical_keyword_hits（默认 2）
# 或至少有一个长度 ≥5 的关键词命中
```

### 5.4 confidence：门槛刻度，不是概率

```python
confidence = min(0.99, max(0.5, score / 100))
```

| score | confidence |
|-------|------------|
| 11 | 0.5 |
| 25 | 0.5 |
| 58 | 0.58 |
| 99 | 0.99 |

score 在 11～49 之间 confidence **全是 0.5**——粗档位，不是精细概率。

**为什么要造这个数：** 与向量侧 `confidence ≈ 1 - distance` **同一量纲**，后面同一套门槛能比较。

**用在哪：**

- `_passes_lexical_only_gate`：confidence ≥ `min_executable_confidence`（0.58）或整句匹配 example → 才放行
- `_passes_executable_routing_gate`：distance、gap、ambiguous 等综合判断
- **不表示**「这个意图有 73% 概率对」；所有意图概率也不归一

向量侧：`confidence = max(0, min(0.99, 1.0 - distance))`  
LLM 精排还有 `extraction.confidence`，第三套数，同样多用于门槛。

### 5.5 Fast path：何时跳过 LLM

满足其一可跳过 LLM 精排：

- 问句与某条 `example` 整句匹配
- Top1 `distance ≤ fast_path_max_distance`（0.22）且与 Top2 差距 ≥ `min_candidate_gap`（0.12）
- 关键词极强：`lexical.distance ≤ strong_lexical_distance`（0.03）

### 5.6 路由门槛一览

配置入口：`configs/performance.yaml` → `intent_routing`

| 参数 | 默认 | 作用 |
|------|------|------|
| `vector_distance_threshold` | 0.45 | 向量正样本 distance 超过则不进候选 |
| `executable_max_distance` | 0.35 | 可执行意图最终 distance 上限 |
| `min_executable_confidence` | 0.58 | 等价 distance ≤ 0.42；关键词也要过线 |
| `min_candidate_gap` | 0.12 | Fast path：Top1/Top2 要拉开 |
| `min_ambiguity_gap` | 0.05 | 两名都近且差距小 → 判 ambiguous，拒硬走 |
| `fast_path_max_distance` | 0.22 | 足够近才跳过 LLM |
| `strong_lexical_distance` | 0.03 | 关键词 override 向量、单独过关的阈值 |
| `min_llm_select_confidence` | 0.72 | LLM 精排后置信不足则拒答 |
| `require_high_confidence_without_llm` | true | 没走 LLM 也必须过 executable gate |
| `min_lexical_keyword_hits` | 2 | 关键词至少命中几个（社区版已用） |

环境变量（向量索引）：`TEXT2SQL_INTENT_VECTOR_DISTANCE_THRESHOLD`（默认 0.62）、`TEXT2SQL_INTENT_VECTOR_TOP_K`（默认 6）。

LLM 策略：`TEXT2SQL_LLM_SLOT_POLICY=always` 强制每条走 LLM（慢但更稳）。

---

## 六、防误命中：按现象选旋钮

### 6.1 意图配置（治本，优先）

| 字段 | 防误命中用法 |
|------|--------------|
| `match.all` | 加必须词，如渠道统计加 `all: ["订单"]` |
| `match.any` | 缩短泛词，少用单独的「渠道」「统计」 |
| `match.none` | 出现则整意图排除 |
| `priority` | 易混意图拉开 priority |
| `examples` | 只放典型问句；误中反例 **不要** 写进 examples |
| `negative_queries` | **重点**：误中过的问句放这里 |
| `boundary_queries` / `boundary_negative_queries` | A/B 两意图抢答时拉开 |

### 6.2 路由参数（控放行）

| 现象 | 优先调什么 |
|------|------------|
| 向量把 A 问成 B | `negative_queries`；`vector_distance_threshold` ↓；`min_candidate_gap` ↑ |
| 关键词一个词就中 | `match.all` / `match.none`；`min_lexical_keyword_hits` ↑ |
| 两意图很像 | `min_ambiguity_gap` ↑；互写 boundary 负样本 |
| 不该跳过 LLM 却跳了 | `fast_path_max_distance` ↓；保持 `require_high_confidence_without_llm: true` |
| LLM 选了仍错 | 补 examples / semantic.queries；`min_llm_select_confidence` ↑ |

### 6.3 对话改写防误触发

- 提高专用规则 `priority`
- 收紧 trigger 正则/上下文条件
- 注意 **首命中即停**：过高 priority 的误规则会挡掉后面正确规则

### 6.4 实操顺序

1. 改 **yaml 意图规则**（none / all / negative_queries）  
2. 收紧 **executable_max_distance、min_executable_confidence**  
3. 仍混则 **min_ambiguity_gap、min_candidate_gap**  
4. 最后 **LLM always** 或补 negative 样本  

---

## 七、规则引擎 vs 训练模型

### 7.1 规则引擎不是「训练」出来的

- 没有反向传播、没有梯度更新。
- 迭代方式是 **数据驱动改配置**：eval case 失败 → 分析原因 → 加规则/改 match/补 negative → CI 回归。

### 7.2 用 LLM 辅助迭代规则（不是替代运行时）

| 阶段 | 做法 |
|------|------|
| 收集 | 线上/测试失败问句、用户纠正 |
| 聚类 | LLM 或脚本按误命中类型分组 |
| 提案 | LLM 建议加 `match.none`、新 rule id、negative_query |
| 验证 | 写入 yaml / 规则表 → `eval_cases/cases.yaml` 回归 |
| 合并 | 人审后合入，CI 锁住不退化 |

这和训练模型的区别：**产物是可读、可审计的配置**，不是黑盒权重。

### 7.3 泛化边界

| 情况 | 做法 |
|------|------|
| 问法不同，SQL 相同 | 补 keywords / semantic.queries |
| 只是分组维度不同 | 合并意图或加 slot；或用 `dimension_slot` 规则 |
| 统计口径不同 | 新意图 + 新模板 |
| 探索性问法、复杂 JOIN、一句话报告 | 走 **Agent OS** 或 guarded 兜底，不硬塞模板 |

---

## 八、安全与治理层（Harness）

### 8.1 白名单 Schema

`configs/whitelist_tables.yaml`：允许的表、字段、JOIN 路径。SQL 护栏拒绝白名单外引用。

### 8.2 租户注入

每张 scoped 表声明 `domain_column`（Demo：`tenant_id`）。运行时强制注入，**不让模型决定查哪个组织**。

### 8.3 SQL 策略

- SELECT-only  
- 敏感值参数化（手机号不进 SQL 明文）  
- 非标量结果自动 LIMIT  
- live 模式可选 EXPLAIN 拒 heavy scan  

### 8.4 结果封装（结束也不一定要 LLM）

- 自然语言摘要：按结果形状套模板句式  
- 表格列名：schema + semantics 映射中文  
- 图表：问句关键词或意图默认值 → ECharts  

---

## 九、Demo 与实验指引

### 9.1 本地启动

```bash
# text2sql-mvp 根目录
./start.sh
# 浏览器打开 chat 页面（默认 8777）
```

### 9.2 建议 demo 路径

1. **完整问句** → 看意图命中、SQL、饼图/表格  
2. **追问「折线图也生成一下」** → Network 里看 `interactionLogs` 的 `rewriteReason=chart_type_follow_up`  
3. **「那按状态呢」**（有上轮分组问句时）→ `dimension_slot_follow_up`  
4. **故意模糊问句** → 看 unsupported / 门槛拒答  

社区场景 test81 跑 **community-text2sql-mvp**；支付 Demo 跑 **text2sql-mvp**。

### 9.3 回归测试

- `eval_cases/cases.yaml`：每条典型问句断言命中表、group_by 等  
- 改配置后 CI 跑一遍，**改 A 不能坏 B**  

---

## 十、收尾：三句话带走

1. **快速模式 = 召回 → 精排 → 模板 → 护栏**；LLM 最多 1 次，不是端到端写 SQL。  
2. **对话改写是轻量规则引擎**；意图识别靠向量 + 关键词 + 门槛；confidence 是刻度不是概率。  
3. **80% 问数走 Text2SQL，20% 复杂任务走 Agent OS**；超出模板假设，诚实拒答或换架构，比强行泛化靠谱。  

---

## 附录 A：关键文件索引

| 文件 | 作用 |
|------|------|
| `docs/architecture.md` | 架构图、十步骨架、规则引擎说明 |
| `configs/business_semantics.yaml` | 意图、match、semantic、模板 |
| `configs/performance.yaml` | 路由门槛 intent_routing |
| `configs/whitelist_tables.yaml` | 表白名单 |
| `conversation_rewrite.py` | 对话改写规则引擎 |
| `business_semantics.py` | 意图识别、路由、plan |
| `intent_vector.py` | 向量召回与 negative 过滤 |
| `eval_cases/cases.yaml` | 回归用例 |

## 附录 B：Mermaid 是什么（若学员问）

一种在 Markdown 里写 **流程图/时序图** 的文本语法，GitHub、VS Code、部分文档站能直接渲染。本仓库 `architecture.md` 里的 flowchart 即 Mermaid，源码可改、可版本管理，比纯图片易维护。
