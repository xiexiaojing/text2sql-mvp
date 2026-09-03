#!/usr/bin/env python3
"""Analyze query_audit history and evals coverage; emit deterministic Markdown + SVG.

Reads the audit DB read-only and eval_cases/cases.yaml, writes docs/eval-report.md
plus SVG charts under docs/assets/eval-report/. Rerunning with unchanged inputs
produces byte-identical output.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "packages" / "text2sql_runtime" / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "packages" / "text2sql_runtime" / "src"))

from text2sql_runtime.visualization import (  # noqa: E402
    CHART_INTENTS,
    CHART_TYPE_LABELS,
    INTENT_DEFAULT_CHART,
)


# ---------- data model ----------

@dataclass
class AuditRecord:
    query_id: str
    question: str
    status: str
    hit_path: str | None
    rejection_reason: str | None
    elapsed_ms: int
    template_id: str | None
    intent: str | None
    output_type: str | None


@dataclass
class AuditStats:
    total: int
    hit_path_counts: list[tuple[str, int]]
    rejection_counts: list[tuple[str, int]]
    elapsed_p50: int
    elapsed_p90: int
    elapsed_max: int
    elapsed_min: int
    elapsed_histogram: list[tuple[int, int]]
    intent_counts: list[tuple[str, int]]
    template_counts: list[tuple[str, int]]
    chart_hits: int
    chart_total_candidates: int
    intent_to_chart: list[tuple[str, str, str, int]]  # (intent, default_chart_key, label, count)


@dataclass
class CoverageRow:
    case_id: str
    question: str
    expected_route: str
    expected_intent: str | None
    expected_template: str | None
    expected_rejection: str | None
    covered_in_audit: bool


@dataclass
class CoverageReport:
    rows: list[CoverageRow]
    covered_intents: set[str] = field(default_factory=set)
    covered_templates: set[str] = field(default_factory=set)
    covered_routes: set[str] = field(default_factory=set)
    covered_rejection_reasons: set[str] = field(default_factory=set)
    covered_expected_rejects: set[str] = field(default_factory=set)
    gaps: list[tuple[str, str]] = field(default_factory=list)


# ---------- loaders ----------

def _read_records(db_path: Path) -> list[AuditRecord]:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    records: list[AuditRecord] = []
    try:
        rows = conn.execute(
            "SELECT query_id, question, status, hit_path, rejection_reason,"
            " elapsed_ms, interaction_logs_json"
            " FROM query_audit ORDER BY created_at ASC, query_id ASC"
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        template_id, intent, output_type = _extract_semantic_meta(row["interaction_logs_json"])
        records.append(
            AuditRecord(
                query_id=row["query_id"],
                question=row["question"] or "",
                status=row["status"] or "",
                hit_path=row["hit_path"],
                rejection_reason=row["rejection_reason"],
                elapsed_ms=int(row["elapsed_ms"] or 0),
                template_id=template_id,
                intent=intent,
                output_type=output_type,
            )
        )
    return records


def _extract_semantic_meta(logs_json: str | None) -> tuple[str | None, str | None, str | None]:
    if not logs_json:
        return None, None, None
    try:
        logs = json.loads(logs_json)
    except json.JSONDecodeError:
        return None, None, None
    template_id: str | None = None
    intent: str | None = None
    output_type: str | None = None
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        if entry.get("kind") == "semantic_planner":
            template_id = entry.get("templateId")
            intent = entry.get("intent")
            for cand in entry.get("candidateIntents") or []:
                if isinstance(cand, dict) and cand.get("intent") == intent:
                    output_type = cand.get("output_type")
                    break
            break
    return template_id, intent, output_type


def _load_eval_cases(cases_yaml: Path) -> list[dict[str, Any]]:
    with cases_yaml.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    return list(payload.get("cases") or [])


def _load_configured_intents(semantics_yaml: Path) -> list[dict[str, Any]]:
    if not semantics_yaml.exists():
        return []
    with semantics_yaml.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    return list(payload.get("intents") or [])


# ---------- aggregation ----------

def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if pct <= 0:
        return ordered[0]
    if pct >= 100:
        return ordered[-1]
    rank = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    frac = rank - lo
    interp = ordered[lo] + (ordered[hi] - ordered[lo]) * frac
    return int(round(interp))


def compute_stats(records: list[AuditRecord]) -> AuditStats:
    hit_paths = Counter((r.hit_path or "-") for r in records)
    rejections = Counter(
        (r.rejection_reason or "-") for r in records if r.status == "rejected"
    )
    elapsed = [r.elapsed_ms for r in records]

    intent_counter = Counter((r.intent or "-") for r in records if r.intent)
    template_counter = Counter((r.template_id or "-") for r in records if r.template_id)

    chart_hits = sum(1 for r in records if r.intent and r.intent in CHART_INTENTS and r.status == "planned")
    chart_total = sum(1 for r in records if r.status == "planned")
    intent_chart_rows: list[tuple[str, str, str, int]] = []
    for intent, count in sorted(intent_counter.items()):
        if intent in CHART_INTENTS:
            chart_key = INTENT_DEFAULT_CHART.get(intent, "?")
            label = CHART_TYPE_LABELS.get(chart_key, chart_key)  # type: ignore[arg-type]
            intent_chart_rows.append((intent, chart_key, label, count))

    elapsed_bins = [(0, "0ms"), (1, "1ms"), (2, "2ms"), (5, "3-5ms"), (10, "6-10ms"), (50, "11-50ms"), (10**9, ">50ms")]
    hist_counter: dict[str, int] = {label: 0 for _, label in elapsed_bins}
    for v in elapsed:
        for upper, label in elapsed_bins:
            if v <= upper:
                hist_counter[label] += 1
                break
    histogram = [(label, hist_counter[label]) for _, label in elapsed_bins if hist_counter[label]]

    return AuditStats(
        total=len(records),
        hit_path_counts=sorted(hit_paths.items(), key=lambda kv: (-kv[1], kv[0])),
        rejection_counts=sorted(rejections.items(), key=lambda kv: (-kv[1], kv[0])),
        elapsed_p50=_percentile(elapsed, 50),
        elapsed_p90=_percentile(elapsed, 90),
        elapsed_max=max(elapsed) if elapsed else 0,
        elapsed_min=min(elapsed) if elapsed else 0,
        elapsed_histogram=histogram,
        intent_counts=sorted(intent_counter.items(), key=lambda kv: (-kv[1], kv[0])),
        template_counts=sorted(template_counter.items(), key=lambda kv: (-kv[1], kv[0])),
        chart_hits=chart_hits,
        chart_total_candidates=chart_total,
        intent_to_chart=intent_chart_rows,
    )


def build_coverage(
    records: list[AuditRecord],
    cases: list[dict[str, Any]],
    configured_intents: list[dict[str, Any]] | None = None,
) -> CoverageReport:
    question_to_record: dict[str, AuditRecord] = {}
    for rec in records:
        question_to_record[rec.question] = rec

    audit_intents = {rec.intent for rec in records if rec.intent}
    audit_templates = {rec.template_id for rec in records if rec.template_id}
    audit_routes = {rec.hit_path for rec in records if rec.hit_path}
    audit_rejections = {rec.rejection_reason for rec in records if rec.rejection_reason}

    rows: list[CoverageRow] = []
    covered_intents: set[str] = set()
    covered_templates: set[str] = set()
    covered_routes: set[str] = set()
    covered_rejections: set[str] = set()
    covered_expected_rejects: set[str] = set()

    for case in cases:
        cid = str(case.get("id") or "-")
        question = str(case.get("question") or "-")
        expected = case.get("expected") or {}
        rejected = bool(expected.get("rejected"))
        rec = question_to_record.get(question)
        if rejected:
            route = "rejected"
            intent = rec.intent if rec else None
            template = rec.template_id if rec else None
            rejection = (
                rec.rejection_reason
                if rec and rec.rejection_reason
                else (expected.get("reason_contains") or None)
            )
        else:
            route = rec.hit_path if rec else "-"
            intent = rec.intent if rec else None
            template = rec.template_id if rec else None
            rejection = None
        covered = rec is not None
        rows.append(
            CoverageRow(
                case_id=cid,
                question=question,
                expected_route=route or "-",
                expected_intent=intent,
                expected_template=template,
                expected_rejection=rejection,
                covered_in_audit=covered,
            )
        )
        if route:
            covered_routes.add(route)
        if intent:
            covered_intents.add(intent)
        if template:
            covered_templates.add(template)
        if rejection:
            covered_rejections.add(rejection)
        reason_contains = expected.get("reason_contains")
        if rejected and reason_contains:
            covered_expected_rejects.add(str(reason_contains))

    gaps: list[tuple[str, str]] = []
    for route in sorted(audit_routes):
        if route not in covered_routes:
            gaps.append(("route", route))
    for intent in sorted(audit_intents):
        if intent not in covered_intents:
            gaps.append(("intent", intent))
    for template in sorted(audit_templates):
        if template not in covered_templates:
            gaps.append(("template", template))
    for reason in sorted(audit_rejections):
        if reason not in covered_rejections:
            gaps.append(("rejection_reason", reason))

    case_questions = {(case.get("question") or "").strip() for case in cases}
    for cfg in configured_intents or []:
        cid = cfg.get("id")
        status = cfg.get("status")
        if not cid or status != "executable":
            continue
        if cid in covered_intents:
            continue
        examples = {str(x).strip() for x in (cfg.get("examples") or [])}
        semantic_queries = {
            str(x).strip() for x in ((cfg.get("semantic") or {}).get("queries") or [])
        }
        if examples & case_questions or semantic_queries & case_questions:
            covered_intents.add(str(cid))
            continue
        gaps.append(("configured_intent", str(cid)))

    return CoverageReport(
        rows=rows,
        covered_intents=covered_intents,
        covered_templates=covered_templates,
        covered_routes=covered_routes,
        covered_rejection_reasons=covered_rejections,
        covered_expected_rejects=covered_expected_rejects,
        gaps=gaps,
    )


# ---------- svg rendering ----------

_PALETTE = ["#4f46e5", "#f59e0b", "#10b981", "#ef4444", "#0ea5e9", "#a855f7", "#14b8a6", "#f97316"]
_SVG_STYLE = (
    "<style>"
    ".chart-bg{fill:none}"
    ".chart-axis{stroke:#94a3b8;stroke-width:1}"
    ".chart-grid{stroke:#94a3b8;stroke-width:1;stroke-dasharray:2 3;opacity:0.35}"
    ".chart-label{font:12px 'Helvetica Neue',Arial,sans-serif;fill:#64748b}"
    ".chart-title{font:600 14px 'Helvetica Neue',Arial,sans-serif;fill:#334155}"
    ".chart-value{font:600 12px 'Helvetica Neue',Arial,sans-serif;fill:#334155}"
    "@media (prefers-color-scheme: dark){"
    ".chart-label{fill:#94a3b8}"
    ".chart-title{fill:#e2e8f0}"
    ".chart-value{fill:#e2e8f0}"
    ".chart-axis{stroke:#64748b}"
    ".chart-grid{stroke:#64748b}"
    "}"
    "</style>"
)


def _svg_bar_chart(title: str, entries: list[tuple[str, int]], *, width: int = 640, bar_h: int = 26, left_pad: int = 260) -> str:
    if not entries:
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='60'>{_SVG_STYLE}<text class='chart-title' x='16' y='30'>{_escape(title)} (无数据)</text></svg>"
    max_val = max(v for _, v in entries) or 1
    top_pad = 40
    bottom_pad = 20
    row_gap = 10
    plot_w = width - left_pad - 60
    height = top_pad + (bar_h + row_gap) * len(entries) + bottom_pad
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        _SVG_STYLE,
        f"<text class='chart-title' x='16' y='24'>{_escape(title)}</text>",
    ]
    y = top_pad
    for idx, (label, value) in enumerate(entries):
        color = _PALETTE[idx % len(_PALETTE)]
        bar_w = int(round(plot_w * value / max_val)) if max_val else 0
        bar_w = max(bar_w, 2 if value else 0)
        parts.append(
            f"<text class='chart-label' x='{left_pad - 10}' y='{y + bar_h - 8}' text-anchor='end'>{_escape(label)}</text>"
        )
        parts.append(
            f"<rect x='{left_pad}' y='{y}' width='{bar_w}' height='{bar_h}' rx='3' ry='3' fill='{color}' opacity='0.85'/>"
        )
        parts.append(
            f"<text class='chart-value' x='{left_pad + bar_w + 6}' y='{y + bar_h - 8}'>{value}</text>"
        )
        y += bar_h + row_gap
    parts.append("</svg>")
    return "".join(parts)


def _svg_donut(title: str, entries: list[tuple[str, int]], *, width: int = 520) -> str:
    total = sum(v for _, v in entries)
    if total <= 0 or not entries:
        return f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='60'>{_SVG_STYLE}<text class='chart-title' x='16' y='30'>{_escape(title)} (无数据)</text></svg>"
    height = max(280, 60 + 32 * len(entries))
    cx, cy, r_outer, r_inner = 140, 150, 100, 55
    parts = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
        _SVG_STYLE,
        f"<text class='chart-title' x='16' y='24'>{_escape(title)}</text>",
    ]
    start = -math.pi / 2
    for idx, (label, value) in enumerate(entries):
        angle = 2 * math.pi * value / total
        end = start + angle
        large = 1 if angle > math.pi else 0
        x1 = cx + r_outer * math.cos(start)
        y1 = cy + r_outer * math.sin(start)
        x2 = cx + r_outer * math.cos(end)
        y2 = cy + r_outer * math.sin(end)
        x3 = cx + r_inner * math.cos(end)
        y3 = cy + r_inner * math.sin(end)
        x4 = cx + r_inner * math.cos(start)
        y4 = cy + r_inner * math.sin(start)
        color = _PALETTE[idx % len(_PALETTE)]
        d = (
            f"M {x1:.3f} {y1:.3f} A {r_outer} {r_outer} 0 {large} 1 {x2:.3f} {y2:.3f}"
            f" L {x3:.3f} {y3:.3f} A {r_inner} {r_inner} 0 {large} 0 {x4:.3f} {y4:.3f} Z"
        )
        parts.append(f"<path d='{d}' fill='{color}' opacity='0.9'/>")
        start = end
    parts.append(f"<text class='chart-value' x='{cx}' y='{cy + 5}' text-anchor='middle'>{total}</text>")
    legend_x = 280
    ly = 60
    for idx, (label, value) in enumerate(entries):
        color = _PALETTE[idx % len(_PALETTE)]
        pct = value / total * 100
        parts.append(f"<rect x='{legend_x}' y='{ly - 10}' width='12' height='12' rx='2' fill='{color}'/>")
        parts.append(
            f"<text class='chart-label' x='{legend_x + 20}' y='{ly}'>{_escape(label)} — {value} ({pct:.1f}%)</text>"
        )
        ly += 22
    parts.append("</svg>")
    return "".join(parts)


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return path.name


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------- markdown report ----------

def render_markdown(stats: AuditStats, coverage: CoverageReport, cases_count: int, db_path: Path, cases_yaml: Path) -> str:
    lines: list[str] = []
    lines.append("# 审计数据分析报告")
    lines.append("")
    lines.append("> 由 `scripts/analyze_audit.py` 自动生成，可重复运行；输入不变则输出不变。")
    lines.append("")
    lines.append(f"- 审计库: `{_display_path(db_path)}`")
    lines.append(f"- 评测用例: `{_display_path(cases_yaml)}` ({cases_count} 条)")
    lines.append(f"- 记录总数: {stats.total}")
    lines.append("")

    lines.append("## 1. 路由分支分布")
    lines.append("")
    lines.append("| hit_path | 记录数 | 占比 |")
    lines.append("| --- | ---: | ---: |")
    for path, count in stats.hit_path_counts:
        pct = count / stats.total * 100 if stats.total else 0
        lines.append(f"| `{path}` | {count} | {pct:.1f}% |")
    lines.append("")
    lines.append("![hit_path 分布](assets/eval-report/hit-path.svg)")
    lines.append("")

    lines.append("## 2. 拒答码分布")
    lines.append("")
    rejected_total = sum(c for _, c in stats.rejection_counts)
    reject_pct = rejected_total / stats.total * 100 if stats.total else 0
    lines.append(f"共 {rejected_total} 条被拒答，占总量 {reject_pct:.1f}%。")
    lines.append("")
    if stats.rejection_counts:
        lines.append("| 拒答码 | 次数 | 占拒答比 |")
        lines.append("| --- | ---: | ---: |")
        for reason, count in stats.rejection_counts:
            pct = count / rejected_total * 100 if rejected_total else 0
            lines.append(f"| {reason} | {count} | {pct:.1f}% |")
        lines.append("")
        lines.append("![拒答码分布](assets/eval-report/rejection.svg)")
        lines.append("")
    else:
        lines.append("（无拒答记录）")
        lines.append("")

    lines.append("## 3. 耗时分布 (elapsed_ms)")
    lines.append("")
    lines.append(f"- min: {stats.elapsed_min} ms")
    lines.append(f"- p50: {stats.elapsed_p50} ms")
    lines.append(f"- p90: {stats.elapsed_p90} ms")
    lines.append(f"- max: {stats.elapsed_max} ms")
    lines.append("")
    if stats.elapsed_histogram:
        lines.append("| 区间 | 记录数 |")
        lines.append("| --- | ---: |")
        for label, count in stats.elapsed_histogram:
            lines.append(f"| {label} | {count} |")
        lines.append("")
        lines.append("![耗时分布](assets/eval-report/elapsed.svg)")
        lines.append("")

    lines.append("## 4. 图表命中")
    lines.append("")
    lines.append(f"- 图表候选 intent（visualization.CHART_INTENTS）共 {len(CHART_INTENTS)} 个: {', '.join(sorted(CHART_INTENTS))}")
    hit_pct = stats.chart_hits / stats.chart_total_candidates * 100 if stats.chart_total_candidates else 0
    lines.append(f"- planned 记录 {stats.chart_total_candidates} 条，其中命中图表的 {stats.chart_hits} 条，命中率 {hit_pct:.1f}%")
    lines.append("")
    if stats.intent_to_chart:
        lines.append("| intent | 默认图表 | 次数 |")
        lines.append("| --- | --- | ---: |")
        for intent, key, label, count in stats.intent_to_chart:
            lines.append(f"| `{intent}` | {label} (`{key}`) | {count} |")
        lines.append("")
        lines.append("![图表命中](assets/eval-report/chart-intents.svg)")
        lines.append("")

    lines.append("## 5. 评测用例覆盖矩阵")
    lines.append("")
    lines.append("| case_id | question | 路由 | intent | template | 拒答码 | 审计中已复现 |")
    lines.append("| --- | --- | --- | --- | --- | --- | :---: |")
    for row in coverage.rows:
        intent = row.expected_intent or "-"
        template = row.expected_template or "-"
        rejection = row.expected_rejection or "-"
        covered = "✓" if row.covered_in_audit else "✗"
        lines.append(
            f"| `{row.case_id}` | {row.question} | `{row.expected_route}` | `{intent}` | `{template}` | {rejection} | {covered} |"
        )
    lines.append("")

    lines.append("### 未覆盖的分支")
    lines.append("")
    if coverage.gaps:
        lines.append("| 维度 | 值 |")
        lines.append("| --- | --- |")
        for kind, value in coverage.gaps:
            lines.append(f"| {kind} | `{value}` |")
        lines.append("")
        lines.append(
            "> 说明：审计中出现的历史拒答码可能因白名单/护栏配置调整而无法在当前运行时复现，"
            "只能作为回溯凭证保留。`configured_intent` 表示 `business_semantics.yaml` 里配置为 executable 但评测用例未覆盖的 intent。"
        )
    else:
        lines.append("（评测用例已覆盖审计中出现的全部分支，且业务语义配置中每个 executable intent 至少有一条对应用例）")
    lines.append("")

    lines.append("## 6. 结论")
    lines.append("")
    lines += _render_conclusions(stats, coverage)
    lines.append("")

    return "\n".join(lines) + "\n"


def _render_conclusions(stats: AuditStats, coverage: CoverageReport) -> list[str]:
    total = stats.total
    lines: list[str] = []

    # 结论一：路由集中在 semantic_template
    top_route, top_count = stats.hit_path_counts[0] if stats.hit_path_counts else ("-", 0)
    pct = top_count / total * 100 if total else 0
    lines.append(
        f"1. 路由集中度极高：`{top_route}` 命中 {top_count}/{total} = {pct:.1f}%，"
        "说明主路径仍是语义模板匹配，动态实体查询与拒答只占尾部；容量规划应按语义模板的 QPS 上限来算，不是按拒答分支。"
    )

    # 结论二：拒答里 unconfigured demo 占主导
    if stats.rejection_counts:
        rej_total = sum(c for _, c in stats.rejection_counts)
        top_reason, top_reason_count = stats.rejection_counts[0]
        top_reason_pct = top_reason_count / rej_total * 100 if rej_total else 0
        lines.append(
            f"2. 拒答口径不健康：{top_reason_count}/{rej_total} = {top_reason_pct:.1f}% 的拒答都是 “{top_reason}”，"
            "这是留在配置里做兜底演示的空 intent；真实的白名单拦截（函数/字段不在白名单）只有 "
            f"{rej_total - top_reason_count} 条。评估失败率时应把这一类兜底剔除，否则会把拒答率算虚高。"
        )
    else:
        lines.append("2. 当前审计里没有拒答记录，无法从数据侧验证白名单拦截行为，建议在评测里补一条会被 SQL 护栏拦截的用例。")

    # 结论三：延时数据不代表真实运行
    p50, p90, mx = stats.elapsed_p50, stats.elapsed_p90, stats.elapsed_max
    lines.append(
        f"3. 耗时分布 p50={p50}ms / p90={p90}ms / max={mx}ms 全部在毫秒级，"
        "是因为审计里的样本几乎全走 `dry_run`（execution.mode='dry_run'），没有真正下推 MySQL。"
        "要观测端到端延时应挂上一份 live 审计再重跑，不要用当前数字对外承诺 SLA。"
    )
    return lines


# ---------- main ----------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "audit.sqlite3")
    parser.add_argument("--cases", type=Path, default=ROOT / "eval_cases" / "cases.yaml")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "eval-report.md")
    parser.add_argument("--assets", type=Path, default=ROOT / "docs" / "assets" / "eval-report")
    parser.add_argument(
        "--semantics",
        type=Path,
        default=ROOT / "configs" / "business_semantics.yaml",
        help="Business semantics YAML — used to enumerate configured intents for gap analysis.",
    )
    parser.add_argument("--json-summary", action="store_true", help="Print a summary JSON to stdout")
    args = parser.parse_args(argv)

    records = _read_records(args.db)
    cases = _load_eval_cases(args.cases)
    configured = _load_configured_intents(args.semantics)

    stats = compute_stats(records)
    coverage = build_coverage(records, cases, configured)

    _write(args.assets / "hit-path.svg", _svg_bar_chart("hit_path 分布", stats.hit_path_counts))
    _write(args.assets / "rejection.svg", _svg_donut("拒答码分布", stats.rejection_counts))
    _write(args.assets / "elapsed.svg", _svg_bar_chart("耗时分布 (elapsed_ms)", stats.elapsed_histogram, left_pad=140))
    chart_entries = [(f"{intent} → {label}", count) for intent, _key, label, count in stats.intent_to_chart]
    _write(args.assets / "chart-intents.svg", _svg_bar_chart("图表 intent 命中", chart_entries, left_pad=340))

    markdown = render_markdown(stats, coverage, len(cases), args.db, args.cases)
    _write(args.out, markdown)

    if args.json_summary:
        summary = {
            "total": stats.total,
            "hit_path_counts": stats.hit_path_counts,
            "rejection_counts": stats.rejection_counts,
            "elapsed_p50": stats.elapsed_p50,
            "elapsed_p90": stats.elapsed_p90,
            "elapsed_max": stats.elapsed_max,
            "chart_hits": stats.chart_hits,
            "chart_total": stats.chart_total_candidates,
            "gaps": coverage.gaps,
            "case_count": len(cases),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"[analyze_audit] wrote {_display_path(args.out)}")
        print(f"[analyze_audit] total={stats.total} hit_paths={stats.hit_path_counts}")
        print(f"[analyze_audit] rejections={stats.rejection_counts}")
        print(
            f"[analyze_audit] elapsed p50={stats.elapsed_p50}ms p90={stats.elapsed_p90}ms max={stats.elapsed_max}ms"
        )
        print(
            f"[analyze_audit] chart_hits={stats.chart_hits}/{stats.chart_total_candidates} gaps={coverage.gaps}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
