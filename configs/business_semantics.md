# 意图匹配算法详解 & 调参指南

## 一、整体流程（两条路径）

### Path A：向量索引禁用时（legacy keyword path）

```
问题 → _best_intent() 关键词打分 → 选最高分意图
  → 若有 required_slots 且 LLM 配置了 → 调用 LLM 提取槽位
  → 否则用启发式正则提取槽位
```

### Path B：向量索引启用时

```
问题 → 向量搜索 top_k=6 → 与关键词匹配合并排序 → 候选列表
  → _try_fast_heuristic_plan() 尝试跳过 LLM（快速路径）
  → 若未命中快速路径且 LLM 策略允许 → 调用 LLM 提取意图+槽位
  → _apply_strong_lexical_override() 关键词覆盖纠偏
```

---

## 二、关键词评分公式（`_best_intent`，所有路径的基石）

对每个 intent，依次检查：

1. `match_none`：任一命中 → 直接排除该 intent
2. `match_all`：若非空且未全部命中 → 跳过该 intent
3. `match_any`：统计命中数 `hits`
4. `examples`：统计精确子串匹配数 `example_hits`
5. `match_any` 非空且 `hits == 0` 且 `example_hits == 0` → 跳过

```
score      = priority + hits + (example_hits × 2)
confidence = min(0.99, max(0.5, score / 100))
distance   = 1.0 - confidence
```

取总分最高的意图。同分时按 YAML 中声明顺序（先声明优先）。

### 关键边界值

| score | confidence | distance | 说明 |
|-------|------------|----------|------|
| ≥ 100 | 0.99 | 0.01 | 满分 |
| 95 | 0.95 | 0.05 | 触发强词覆盖的阈值 (`_STRONG_LEXICAL_DISTANCE`) |
| 80 | 0.80 | 0.20 | 典型中间值 |
| 50 | 0.50 | 0.50 | 最低置信度 |
| ≤ 49 | 0.50 | 0.50 | 不设更低 |

---

## 三、快速路径跳过 LLM 的条件（`_should_skip_llm_for_intent`）

满足以下任一条件即可跳过 LLM，使用启发式槽位提取：

| 条件 | 详情 |
|------|------|
| **a) 精确示例匹配** | 问题与某 example 完全匹配（去空格后），或 example 是问题的子串 |
| **b) 向量距离极小** | 向量距离 ≤ 0.35 且：（只有 1 个候选意图）或（第二名与第一名距离差 ≥ 0.08） |
| **c) 强关键词重合** | 关键词匹配的 intent 与向量第一候选相同，且关键词 distance ≤ 0.05 |

---

## 四、关键词纠偏覆盖（`_apply_strong_lexical_override`）

当 LLM 选择的意图与关键词匹配结果不同时，若关键词匹配 `distance ≤ 0.05`，则用关键词意图覆盖 LLM 结果。

> **这是防止 LLM 误判的最后一道防线，但要求 confidence ≥ 0.95。**

---

## 五、调参策略

| 参数 | 调优建议 |
|------|----------|
| **priority** | 基线值。同类问题中，更具体的意图设置更高优先级。建议 80~95，每次调整 ±2~3。典型：总数类 82~88，聚合统计类 80~86。 |
| **match_all** | 必须同时出现的关键词（AND 逻辑），谨慎使用。过多会增加"零命中导致跳过"的风险。示例：`match_all: ["用户"]` 确保问题涉及用户域。 |
| **match_any** | 任一命中 +1 分的特征词（OR 逻辑）。每个命中贡献 1 分。**要达成 distance ≤ 0.05 需要 score ≥ 95**：`priority 80 + hits 15 = 95`，即需要 match_any 命中 15 个关键词。**建议**：给关键意图增加冗余同义关键词以提高 hits 数（例如同时配 `"下级"` `"下属"` `"子部门"` `"各部门"`）。 |
| **match_none** | 排除关键词（任一命中则跳过整个 intent）。用于排除明显不属于该意图的问句。 |
| **examples** | 精确子串匹配，每个命中 **+2 分**（权重是 match_any 的 2 倍）。同时也是快速路径的条件之一。建议覆盖最典型的自然语言问法。注意：是子串匹配，非完全匹配。示例：`"各中心有多少人"` 可命中 `"要求列举各中心有多少人"`。 |
| **required_slots** | 需要从问句中提取的参数（触发 LLM）。未填充 → status 变为 `needs_clarification`。配合 `slot_defaults` 提供降级默认值。 |
| **semantic.queries** | 语义向量搜索的查询变体，仅在向量索引启用时生效。用于提升向量匹配精度，不影响关键词评分。 |

---

## 六、案例分析

### 为什么"人力资源中心下级各部门人数"不走 `user_by_parent_dept`

**关键词评分过程：**

| intent | 评分 | confidence | distance |
|--------|------|------------|----------|
| `user_by_parent_dept` | `84 + 1("下级") + 0 = 85` | 0.85 | 0.16 |
| `user_by_dept_level` | `match_any=["级部门"]`, "级部门"不连续("下级各部门") → hits=0, example_hits=0 → **被跳过** | — | — |
| `user_count_by_dept` | `match_any=["有多少人","多少人","几个"...]` → 0 命中 → **被跳过** | — | — |

**结论**：`user_by_parent_dept` 的 distance = 0.16 > 0.05，无法触发"强词覆盖" → 若 LLM/向量选错，没有纠偏能力。

### 修复方案

要达到 `distance ≤ 0.05`（触发强词覆盖），需要 `score ≥ 95`：

| 方案 | 计算 | 可行性 |
|------|------|--------|
| 增加 match_any 关键词 | `84 + hits + 0 ≥ 95` → `hits ≥ 11` | 不现实，需要 11 个关键词命中 |
| **提升 priority** | `92 + 3 = 95` → distance = 0.05 | **推荐**：priority 调到 90~92 + 3~4 个 match_any 命中 |
| 添加匹配 example | `84 + 2 + 2×1 = 88` → distance = 0.12 | 仍然不够 |
