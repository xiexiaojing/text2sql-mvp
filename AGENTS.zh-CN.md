# 仓库指南

## 项目结构 & 模块组织

这是一个 mysql + Python 3.11 以上版本的单体仓库 ，通过 `pyproject.toml` 将三个 `src/`-layout 包组织在一起：

| 层级 | 目录 | 职责 |
|------|------|------|
| HTTP API | `apps/api/src/text2sql_api/` | FastAPI 接口 + 内置聊天界面 (`/chat`) |
| MCP 服务 | `apps/mcp/src/text2sql_mcp/` | MCP 工具，暴露同一运行时能力 |
| 核心运行时 | `packages/text2sql_runtime/src/text2sql_runtime/` | 全部业务逻辑（31 个模块） |
| 配置 | `configs/` | 以声明式方式驱动运行时行为的 YAML 文件 |
| 评测 | `eval_cases/` | 回归测试用例 |
| 测试 | `tests/` | Pytest 测试套件（15 个文件） |
| 脚本 | `scripts/` | 表结构探查、JPA 元数据提取、评测运行器 |


## 构建服务与访问

需要windows系统已安装python 3.11 以上版本 和 Git Bash 命令行工具。

**启动/停止 服务：**
```
- Linux/macOS: `./start.sh` / `./stop.sh`
- Windows（Git Bash）: `bash winKaiShi.sh` / `bash winTingZhi.sh`
```

**访问页面：**
- http://127.0.0.1:8777/chat

- **默认 Dry-run**：默认未连接数据库，只是测试服务是否启动成功。开发时需将 `.env.example` 复制为 `.env.local` 存放本地密钥（不要提交密钥或凭据到github等仓库）。在 `.env.local` 中设置 `TEXT2SQL_EXECUTION_MODE=live` 并提供 MySQL 连接信息即可切换。mysql测试用例在scripts/create_tables.sql。

## 架构与请求流程（纵深防御）

每个自然语言问题都会经过层层安全检查管道：

```
问题
 → 对话改写（追问上下文处理：图表类型切换、维度切换）
 → 业务语义路由器（多阶段意图匹配）
    ├─ 精确示例匹配（快速路径，跳过 LLM）
    ├─ 词汇匹配（match.all / match.any / match.none 规则）
    ├─ 向量相似度（可选，基于 OpenAI 兼容的 Embedding）
    └─ LLM 槽位提取（由 TEXT2SQL_LLM_SLOT_POLICY 控制）
 → 语义计划结果：
    ├─ executable template → SQL 模板编译器（参数化，无需 LLM）
    ├─ guarded_text2sql   → Schema-Driven 或 LLM SQL 生成器（见下文）
    ├─ needs_mapping       → 拒绝："此问题暂未配置"
    └─ metadata            → 字段解释路径
 → SQL 策略：注入 `tenant_id = %(domain_id)s` + 确保 LIMIT
 → SQL 守卫：9 项 AST 级别检查（仅 SELECT、无 DML/DDL/子查询、表/列/连接/函数白名单校验）
 → EXPLAIN 代价检查：扫描行 > 50 万或涉及 > 6 张表则拒绝
 → 执行器：DryRun（默认）或 Live MySQL（PyMySQL 只读 或 MCP 委托）
 → 格式化器：人类可读的回答 + 表格 + 可选 ECharts 可视化（12+ 种图表类型）
 → 审计：每次查询写入 SQLite 审计日志
```

**核心设计原则：**
- **白名单优先**：只有 `configs/whitelist_tables.yaml` 中列出的表/列/连接在运行时可达。
- **语义优先**：已知的业务问题优先匹配预审过的 SQL 模板，只有匹配失败时才回退到 LLM。
- **双路径生成**：当语义匹配不到模板但意图被识别时（`guarded_text2sql`），系统在 `SchemaDrivenSqlGenerator`（基于规则，从关键词推断查询形态 + BFS 连接解析）和 `OpenAICompatibleSqlGenerator`（基于 LLM，同时支持 OpenAI 和 Anthropic 传输协议）之间选择。路由器根据意图置信度和 `TEXT2SQL_LLM_SLOT_POLICY` 决定。

## 关键模块职责

中央协调器是 `service.py`（725 行）—— `Text2SqlService` 组装所有组件并定义端到端的查询管道。

| 模块 | 职责 |
|------|------|
| `business_semantics.py`（1344 行） | 意图匹配引擎，多阶段匹配 + SQL 模板编译及槽位绑定 |
| `generator.py` | `SchemaDrivenSqlGenerator`（基于规则）+ `OpenAICompatibleSqlGenerator`（基于 LLM，JSON 响应解析） |
| `sql_guard.py` | 依据白名单验证生成 SQL：表、列、连接、函数、域过滤器、敏感列检查 |
| `sql_policy.py` | 为限域表注入 `WHERE tenant_id = %(domain_id)s`；添加/限制 LIMIT |
| `executor.py` | 三种后端：`DryRunExecutor`、`MySqlReadOnlyExecutor`（PyMySQL）、`MySqlMcpExecutor`（MCP 委托） |
| `visualization.py`（772 行） | 构建饼图/柱状图/折线图/雷达图/玫瑰图/漏斗图/桑基图/瀑布图/散点图/热力图的 ECharts JSON 配置 |
| `conversation.py` | 追问处理：图表类型切换（饼图→折线图）、维度切换（"那按状态呢"）、从历史继承主题 |
| `formatter.py` | 生成人类可读的回答 + 结构化表格数据、标量计数模板、实体标签映射 |
| `audit.py` | `SQLiteAuditStore` — 记录每次查询的问题、SQL、状态、耗时、EXPLAIN、交互日志 |
| `context.py` | 从语义概念、候选表、对话历史构建 LLM 提示词上下文 |
| `config.py` | `RuntimeSettings` — 从环境变量（`.env.local`）和 `configs/performance.yaml` 读取全部配置 |
| `router.py` | 问题策略校验（禁止导出/跨域）、表估计、基于 EXPLAIN 的拒绝阈值 |

## 配置文件（声明式系统大脑）

这些 YAML 文件驱动运行时行为——修改它们即可改变系统行为，无需改动代码：

| 文件 | 流向 | 作用 |
|------|------|------|
| `configs/whitelist_tables.yaml` | `SchemaCatalog` → `SqlGuard`、`SqlPolicy`、`SchemaContextBuilder`、`Router` | 定义允许的表、列、索引、连接路径、域列、行数估计 |
| `configs/business_semantics.yaml` | `BusinessSemanticIndex` | 定义意图（匹配规则 + 示例）、SQL 模板、实体映射、输出类型 |
| `configs/semantic_overrides.yaml` | `SemanticIndex` → `Router`、`SchemaContextBuilder` | 轻量级关键词概念检测（Demo 中 3 个概念） |
| `configs/performance.yaml` | `Router`、`SqlGuard`、`SqlPolicy` | 超时（预检 5s，强制终止 30s）、行数限制（默认 200、最大 1000）、允许的 SQL 函数、EXPLAIN 阈值 |

**添加新业务指标时**：先更新 `business_semantics.yaml`（添加意图 + 含 `{domain_id}`、`{result_limit}` 槽位的 SQL 模板），如需新表/列再更新 `whitelist_tables.yaml`。可使用 `scripts/introspect_schema.py` 从线上 MySQL 的 `information_schema` 生成白名单。



## 测试与开发命令

**前台 API 服务：** `./scripts/start_api.sh` 在 8777 端口直接运行 uvicorn。

**运行测试：** `pytest`（默认 dry_run 模式，无需数据库）。使用 `pytest -m "not live"` 显式跳过需要真实数据库的测试。

**运行评测回归：** `PYTHONPATH=apps/api/src:packages/text2sql_runtime/src python scripts/run_evals.py`

**从 MySQL 生成表结构：** `python scripts/introspect_schema.py --host ... --user ... --database ... --output configs/whitelist_tables.yaml`

## 评测用例

回归用例位于 `eval_cases/cases.yaml`。每个用例指定一个问题、预期涉及的表、预期特性以及是否预期被拒绝。用例通过 `scripts/run_evals.py` 运行，它会在 dry_run 模式下加载 `Text2SqlService`，逐一处理每个用例并输出 JSON 格式的实际 vs 预期对比结果。添加新意图或修改 SQL 模板时应同步添加用例。

## 代码风格与命名规范

使用 4 空格缩进，函数/模块用 `snake_case`，类用 `PascalCase`，文件维持在现有包结构中。格式化遵循 `pyproject.toml` 中的 `ruff` 和 `black` 设置：100 字符行宽，Python 3.11 目标，通过 `ruff` 进行 import 排序。优先使用小而明确的函数，而非密集的辅助链式调用。

## 测试规范

`pytest` 是测试运行器。测试文件遵循 `tests/test_*.py` 命名，共享 fixture 位于 `tests/conftest.py`。`conftest.py` 提供 `service` fixture，包含 `dry_run` 模式 + `allow_sensitive_fields=True` 的 `Text2SqlService`。仅在需要真实 MySQL 只读数据库的测试中使用 `@pytest.mark.live` 标记。凡涉及 SQL 守卫、路由、格式化器或配置的行为变更都应添加或更新测试。

## 提交与 PR 规范

近期提交使用 `feat(...)`、`chore:`、`fix:` 等约定式前缀。保持提交聚焦且描述清晰。PR 应说明行为变更、列出涉及修改的配置文件，并附上测试或评测结果。仅 UI 变更需要附截图。

## 安全与配置提示

将 `.env.example` 复制为 `.env.local` 存放本地密钥。不要提交密钥或线上凭据。修改数据库、API 密钥或租户处理逻辑前请阅读 `SECURITY.md`。关键环境变量：`TEXT2SQL_LLM_API_KEY`、`TEXT2SQL_LLM_BASE_URL`、`TEXT2SQL_LLM_MODEL`（LLM 功能）；`TEXT2SQL_DB_HOST` / `TEXT2SQL_DB_USER` / `TEXT2SQL_DB_PASSWORD` / `TEXT2SQL_DB_NAME`（线上 MySQL 模式）。
