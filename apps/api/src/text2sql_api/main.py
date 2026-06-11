import json
import os
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from text2sql_runtime.evals import run_eval_cases
from text2sql_runtime.models import QueryInput
from text2sql_runtime.service import Text2SqlService

app = FastAPI(title="text2sql-mvp", version="0.1.0")


def _camel_case(value: str) -> str:
    chunks = value.split("_")
    return chunks[0] + "".join(chunk.capitalize() for chunk in chunks[1:])


@lru_cache(maxsize=1)
def service() -> Text2SqlService:
    return Text2SqlService.from_project_root()


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "text2sql-mvp", "status": "ok", "chat": "/chat"}


@app.get("/health")
def health() -> dict[str, str]:
    current_service = service()
    mode = "live" if current_service.settings.live_execution else "dry_run"
    llm = "configured" if current_service.settings.llm.configured else "fallback"
    return {
        "status": "ok",
        "execution_mode": mode,
        "executor_backend": current_service.settings.executor_backend,
        "allow_sensitive_fields": str(current_service.settings.allow_sensitive_fields).lower(),
        "llm": llm,
        "llm_policy": current_service.settings.llm.policy,
    }


@app.get("/chat", response_class=HTMLResponse)
def chat() -> str:
    default_domain = os.getenv("TEXT2SQL_DEFAULT_DOMAIN_ID", "demo-tenant-1")
    default_domain_json = json.dumps(default_domain, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Text2SQL MVP 查询</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f9;
      color: #172033;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; }}
    main {{ max-width: 1540px; margin: 0 auto; padding: 24px; }}
    header {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 24px; font-weight: 700; }}
    .status {{ color: #5d6678; font-size: 13px; }}
    .layout {{ display: grid; grid-template-columns: 300px minmax(0, 1fr) 430px; gap: 16px; align-items: start; }}
    .panel, .chatbox, .logpanel {{ background: #fff; border: 1px solid #dde2ea; border-radius: 8px; }}
    .panel, .logpanel {{ padding: 16px; }}
    label {{ display: block; margin-bottom: 6px; color: #485368; font-size: 13px; font-weight: 600; }}
    input, textarea {{
      width: 100%;
      border: 1px solid #cdd5e1;
      border-radius: 6px;
      padding: 10px 12px;
      font-size: 14px;
      color: #172033;
      background: #fff;
    }}
    textarea {{ min-height: 104px; resize: vertical; line-height: 1.5; }}
    .field {{ margin-bottom: 14px; }}
    .check {{ display: flex; align-items: center; gap: 8px; margin: 10px 0 16px; color: #485368; font-size: 14px; }}
    .check input {{ width: auto; }}
    button {{
      border: 0;
      border-radius: 6px;
      padding: 10px 14px;
      font-size: 14px;
      font-weight: 650;
      cursor: pointer;
    }}
    .primary {{ width: 100%; background: #1f6feb; color: #fff; }}
    .primary:disabled {{ background: #94a3b8; cursor: wait; }}
    .examples {{ display: grid; gap: 8px; margin-top: 16px; }}
    .example {{ text-align: left; color: #1f3b63; background: #edf4ff; }}
    .chatbox {{ min-height: 640px; display: flex; flex-direction: column; overflow: hidden; }}
    .messages {{ flex: 1; padding: 18px; overflow: auto; display: grid; align-content: start; gap: 12px; }}
    .msg {{ max-width: 88%; padding: 12px 14px; border-radius: 8px; line-height: 1.55; font-size: 14px; white-space: pre-wrap; }}
    .user {{ justify-self: end; background: #1f6feb; color: #fff; }}
    .assistant {{ justify-self: start; background: #f0f3f8; color: #172033; }}
    .meta {{ color: #667085; font-size: 12px; margin-top: 6px; }}
    .composer {{ border-top: 1px solid #dde2ea; padding: 14px; display: grid; grid-template-columns: 1fr 108px; gap: 10px; }}
    .composer textarea {{ min-height: 54px; max-height: 160px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; background: #fff; }}
    th, td {{ border: 1px solid #d8dee9; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f7f9fc; font-weight: 650; }}
    pre {{ margin: 10px 0 0; padding: 10px; overflow: auto; border-radius: 6px; background: #111827; color: #e5e7eb; font-size: 12px; white-space: pre; }}
    .logpanel {{ min-height: 640px; max-height: calc(100vh - 48px); display: flex; flex-direction: column; overflow: hidden; }}
    .loghead {{ display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
    .loghead h2 {{ margin: 0; font-size: 16px; }}
    .loghint {{ color: #667085; font-size: 12px; }}
    .logs {{ overflow: auto; display: grid; gap: 10px; padding-right: 2px; }}
    .loggroup {{ border: 1px solid #dde2ea; border-radius: 8px; padding: 10px; background: #fbfcfe; }}
    .logtitle {{ color: #172033; font-size: 13px; font-weight: 700; margin-bottom: 8px; }}
    .logitem {{ border-top: 1px solid #e6ebf2; padding-top: 8px; margin-top: 8px; }}
    .logmeta {{ color: #667085; font-size: 12px; line-height: 1.5; }}
    .logitem summary {{ cursor: pointer; color: #1f3b63; font-size: 12px; font-weight: 650; margin-top: 6px; }}
    .logitem pre {{ max-height: 220px; background: #0f172a; }}
    .emptylogs {{ color: #667085; font-size: 13px; line-height: 1.5; }}
    .error {{ background: #fff1f1; color: #9f1d20; }}
    @media (max-width: 860px) {{
      main {{ padding: 14px; }}
      header {{ display: block; }}
      .layout {{ grid-template-columns: 1fr; }}
      .chatbox, .logpanel {{ min-height: 520px; }}
      .msg {{ max-width: 100%; }}
      .composer {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Text2SQL MVP 查询</h1>
        <div class="status" id="health">正在检查服务状态...</div>
      </div>
      <div class="status">只读查询 · 自动注入 domain_id · 危险 SQL 拒绝</div>
    </header>
    <section class="layout">
      <aside class="panel">
        <div class="field">
          <label for="domainId">domainId</label>
          <input id="domainId" value="{escape(default_domain)}" autocomplete="off" />
        </div>
        <label class="check">
          <input id="allowSql" type="checkbox" checked />
          返回生成 SQL
        </label>
        <label class="check">
          <input id="forceLlm" type="checkbox" />
          强制尝试 LLM
        </label>
        <button class="primary" id="sideSend" type="button">发送查询</button>
        <div class="examples">
          <button class="example" type="button">支付订单总数是多少</button>
          <button class="example" type="button">支付订单按渠道统计</button>
          <button class="example" type="button">支付订单按状态统计</button>
          <button class="example" type="button">各支付渠道交易金额分布</button>
          <button class="example" type="button">近7天每日退款笔数趋势</button>
          <button class="example" type="button">商户交易金额排名</button>
        </div>
      </aside>
      <section class="chatbox">
        <div class="messages" id="messages">
          <div class="msg assistant">输入自然语言问题后发送。当前默认组织域是 {escape(default_domain)}。</div>
        </div>
        <div class="composer">
          <textarea id="question" placeholder="例如：支付订单按渠道统计"></textarea>
          <button class="primary" id="send" type="button">发送</button>
        </div>
      </section>
      <aside class="logpanel">
        <div class="loghead">
          <h2>交互日志</h2>
          <span class="loghint">LLM 请求 / 响应 / 耗时</span>
        </div>
        <div class="logs" id="logs">
          <div class="emptylogs">暂无日志。发送查询后会显示本次是否调用 LLM、请求内容、响应内容和耗时。</div>
        </div>
      </aside>
    </section>
  </main>
  <script>
    const defaultDomain = {default_domain_json};
    const messages = document.getElementById("messages");
    const question = document.getElementById("question");
    const domainId = document.getElementById("domainId");
    const allowSql = document.getElementById("allowSql");
    const forceLlm = document.getElementById("forceLlm");
    const logs = document.getElementById("logs");
    const send = document.getElementById("send");
    const sideSend = document.getElementById("sideSend");
    const conversationHistory = [];
    domainId.value = domainId.value || defaultDomain;

    async function loadHealth() {{
      try {{
        const response = await fetch("/health");
        const data = await response.json();
        document.getElementById("health").textContent =
          `服务正常，当前模式：${{data.execution_mode}}/${{data.executor_backend}} · 敏感字段：${{data.allow_sensitive_fields}} · LLM：${{data.llm}}/${{data.llm_policy}}`;
      }} catch (error) {{
        document.getElementById("health").textContent = "服务状态检查失败";
      }}
    }}

    function addMessage(role, html, extraClass = "") {{
      const node = document.createElement("div");
      node.className = `msg ${{role}} ${{extraClass}}`;
      node.innerHTML = html;
      messages.appendChild(node);
      messages.scrollTop = messages.scrollHeight;
      return node;
    }}

    function escapeHtml(value) {{
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }}

    function renderTable(table) {{
      if (!table || !Array.isArray(table.columns) || !Array.isArray(table.rows) || table.rows.length === 0) {{
        return "";
      }}
      const headerLabels = Array.isArray(table.column_labels) && table.column_labels.length === table.columns.length
        ? table.column_labels
        : table.columns;
      const head = headerLabels.map((column) => `<th>${{escapeHtml(column)}}</th>`).join("");
      const rows = table.rows.map((row) => {{
        return `<tr>${{table.columns.map((column) => `<td>${{escapeHtml(row[column])}}</td>`).join("")}}</tr>`;
      }}).join("");
      return `<table><thead><tr>${{head}}</tr></thead><tbody>${{rows}}</tbody></table>`;
    }}

    function renderResult(data) {{
      const parts = [];
      parts.push(`<strong>${{escapeHtml(data.status)}}</strong>`);
      if (data.answer) parts.push(`<div>${{escapeHtml(data.answer)}}</div>`);
      if (data.rejectionReason) parts.push(`<div>${{escapeHtml(data.rejectionReason)}}</div>`);
      parts.push(renderTable(data.table));
      if (data.generatedSql) parts.push(`<pre>${{escapeHtml(data.generatedSql)}}</pre>`);
      parts.push(`<div class="meta">queryId: ${{escapeHtml(data.queryId)}} · hitPath: ${{escapeHtml(data.hitPath)}} · ${{escapeHtml(data.elapsedMs)}} ms</div>`);
      return parts.filter(Boolean).join("");
    }}

    function renderInteractionLogs(data, questionText) {{
      if (logs.querySelector(".emptylogs")) logs.innerHTML = "";
      const group = document.createElement("div");
      group.className = "loggroup";
      const items = Array.isArray(data.interactionLogs) ? data.interactionLogs : [];
      const title = document.createElement("div");
      title.className = "logtitle";
      title.textContent = `${{questionText}} · ${{data.hitPath || "unknown"}} · ${{data.elapsedMs ?? 0}} ms`;
      group.appendChild(title);
      if (items.length === 0) {{
        const empty = document.createElement("div");
        empty.className = "logmeta";
        empty.textContent = "本次没有可展示的交互日志。";
        group.appendChild(empty);
      }} else {{
        for (const item of items) group.appendChild(renderLogItem(item));
      }}
      logs.prepend(group);
    }}

    function renderLogItem(item) {{
      const node = document.createElement("div");
      node.className = "logitem";
      const meta = document.createElement("div");
      meta.className = "logmeta";
      const pieces = [
        item.kind || "log",
        item.status ? `status=${{item.status}}` : "",
        item.intent ? `intent=${{item.intent}}` : "",
        item.templateId ? `template=${{item.templateId}}` : "",
        item.transport ? `transport=${{item.transport}}` : "",
        item.model ? `model=${{item.model}}` : "",
        item.elapsedMs !== undefined ? `${{item.elapsedMs}} ms` : "",
      ].filter(Boolean);
      meta.textContent = pieces.join(" · ");
      node.appendChild(meta);
      if (item.error) {{
        const error = document.createElement("div");
        error.className = "logmeta";
        error.textContent = `error=${{item.error}}`;
        node.appendChild(error);
      }}
      appendDetails(node, "request", item.request);
      appendDetails(node, "response", item.response || item.responseMeta);
      appendDetails(node, "parsed", item.parsed);
      appendDetails(node, "slots", item.slots);
      appendDetails(node, "needs", item.needs);
      appendDetails(node, "semantic-sql", item.kind === "semantic_template" ? {{
        sql: item.sql,
        paramKeys: item.paramKeys,
        plan: item.plan
      }} : null);
      appendDetails(node, "conversation", item.kind === "conversation" ? {{
        originalQuestion: item.originalQuestion,
        effectiveQuestion: item.effectiveQuestion,
        history: item.history
      }} : null);
      appendDetails(node, "fallback", item.kind === "fallback" ? item : null);
      return node;
    }}

    function appendDetails(parent, label, value) {{
      if (!value) return;
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = label;
      const pre = document.createElement("pre");
      pre.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
      details.appendChild(summary);
      details.appendChild(pre);
      parent.appendChild(details);
    }}

    async function submitQuestion() {{
      const text = question.value.trim();
      if (!text) return;
      const currentDomain = domainId.value.trim();
      if (!currentDomain) {{
        addMessage("assistant", "请先填写 domainId。", "error");
        return;
      }}
      addMessage("user", escapeHtml(text));
      question.value = "";
      send.disabled = true;
      sideSend.disabled = true;
      const pending = addMessage("assistant", "查询中...");
      try {{
        const response = await fetch("/v1/query", {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{
            question: text,
            domainId: currentDomain,
            userId: "chat-ui",
            allowReturnSql: allowSql.checked,
            forceLlm: forceLlm.checked,
            history: conversationHistory.slice(-8)
          }})
        }});
        const data = await response.json();
        pending.innerHTML = response.ok ? renderResult(data) : escapeHtml(JSON.stringify(data, null, 2));
        renderInteractionLogs(data, text);
        if (!response.ok) pending.classList.add("error");
        conversationHistory.push({{role: "user", content: text}});
        conversationHistory.push({{
          role: "assistant",
          content: data.answer || data.rejectionReason || data.status || "无结果"
        }});
        if (conversationHistory.length > 12) conversationHistory.splice(0, conversationHistory.length - 12);
      }} catch (error) {{
        pending.textContent = `请求失败：${{error}}`;
        pending.classList.add("error");
      }} finally {{
        send.disabled = false;
        sideSend.disabled = false;
        question.focus();
      }}
    }}

    document.querySelectorAll(".example").forEach((button) => {{
      button.addEventListener("click", () => {{
        question.value = button.textContent.trim();
        question.focus();
      }});
    }});
    send.addEventListener("click", submitQuestion);
    sideSend.addEventListener("click", submitQuestion);
    question.addEventListener("keydown", (event) => {{
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") submitQuestion();
    }});
    loadHealth();
  </script>
</body>
</html>"""


@app.post("/v1/query")
def query(payload: dict[str, Any]) -> dict[str, Any]:
    result = service().query(
        QueryInput(
            question=_required_str(payload, "question"),
            domain_id=_required_str(payload, "domainId", "domain_id"),
            history=_optional_history(payload),
            user_id=_optional_str(payload, "userId", "user_id"),
            allow_return_sql=bool(payload.get("allowReturnSql", payload.get("allow_return_sql", False))),
            max_rows=_optional_int(payload, "maxRows", "max_rows"),
            force_llm=bool(payload.get("forceLlm", payload.get("force_llm", False))),
        )
    )
    return _model_dump(result)


@app.post("/v1/query/estimate")
def estimate(payload: dict[str, Any]) -> dict[str, Any]:
    return _model_dump(
        service().estimate(
            _required_str(payload, "question"),
            _required_str(payload, "domainId", "domain_id"),
            _optional_history(payload),
        )
    )


@app.get("/v1/audit/unsupported")
def unsupported_questions(limit: int = 50, sinceMs: Optional[int] = None) -> dict[str, Any]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    if sinceMs is not None and sinceMs < 0:
        raise HTTPException(status_code=422, detail="sinceMs must be non-negative")
    return service().unsupported_questions(limit=limit, since_ms=sinceMs)


@app.get("/v1/audit/{query_id}")
def audit(query_id: str) -> dict[str, Any]:
    record = service().audit(query_id)
    if record is None:
        raise HTTPException(status_code=404, detail="query_id not found")
    return record


@app.get("/v1/schema/summary")
def schema_summary() -> dict[str, Any]:
    return service().schema_summary()


@app.get("/v1/evals/run")
def evals_run() -> dict[str, Any]:
    root_dir = service().settings.project_root
    return run_eval_cases(service(), Path(root_dir) / "eval_cases" / "cases.yaml")


def _model_dump(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return _dataclass_to_dict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True)
    return dict(value)


def _dataclass_to_dict(value: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in value.__dataclass_fields__:
        item = getattr(value, key)
        result[_camel_case(key)] = item
    return result


def _required_str(payload: dict[str, Any], *keys: str) -> str:
    value = _first_value(payload, *keys)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=422, detail=f"Missing required string field: {keys[0]}")
    return value.strip()


def _optional_str(payload: dict[str, Any], *keys: str) -> Optional[str]:
    value = _first_value(payload, *keys)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"Expected string field: {keys[0]}")
    return value


def _optional_int(payload: dict[str, Any], *keys: str) -> Optional[int]:
    value = _first_value(payload, *keys)
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Expected integer field: {keys[0]}") from exc
    if parsed < 1 or parsed > 1000:
        raise HTTPException(status_code=422, detail=f"Field out of range 1..1000: {keys[0]}")
    return parsed


def _optional_history(payload: dict[str, Any]) -> list[dict[str, str]]:
    value = _first_value(payload, "history", "messages")
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail="Expected list field: history")
    history: list[dict[str, str]] = []
    for item in value[-8:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        history.append({"role": role, "content": content.strip()[:600]})
    return history


def _first_value(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None
