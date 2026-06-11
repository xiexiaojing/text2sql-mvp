from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path

from .audit import SQLiteAuditStore
from .business_semantics import BusinessSemanticIndex, SemanticPlan
from .config import RuntimeSettings, load_settings
from .context import SchemaContextBuilder
from .conversation import contextualize_question
from .executor import SqlExecutor, build_executor
from .field_explanation import explain_field, resolve_field
from .column_labels import EntityColumnLabelIndex
from .formatter import ResultFormatter, _answer_grid_building_list
from .generator import OpenAICompatibleSqlGenerator, SchemaDrivenSqlGenerator
from .models import EstimateResult, ExecutionResult, GeneratedSql, QueryInput, QueryResult, RejectedQuery
from .sql_guard import GuardResult
from .router import QueryRouter
from .schema import SchemaCatalog
from .semantics import SemanticIndex
from .sql_guard import SqlGuard
from .sql_policy import ensure_limit, inject_domain_filter
from .visualization import append_echarts_fence, maybe_build_chart, maybe_build_generic_distribution_chart

_EXECUTABLE_STATUSES = frozenset({"executable", "guarded_text2sql", "metadata"})

_SELF_SCOPED_INTENTS = frozenset(
    {
        "visiting_pending_household_count",
        "visiting_pending_tasks_list",
        "visiting_self_rank",
        "visiting_colleague",
    }
)


def _apply_query_actor(plan: SemanticPlan, query_input: QueryInput, question: str) -> SemanticPlan:
    from dataclasses import replace

    needs_user = "我" in question or plan.intent in _SELF_SCOPED_INTENTS
    if not needs_user:
        return plan
    if query_input.user_id:
        slots = dict(plan.slots)
        slots["current_user_id"] = query_input.user_id
        missing = [slot for slot in plan.missing_slots if slot != "current_user_id"]
        status = plan.status
        reason = plan.reason
        if status == "needs_clarification" and not missing:
            status = "executable"
            reason = None
        return replace(plan, slots=slots, missing_slots=missing, status=status, reason=reason)
    if "我" in question or plan.intent in {
        "visiting_pending_household_count",
        "visiting_colleague",
        "visiting_self_rank",
    }:
        return replace(
            plan,
            status="needs_clarification",
            reason="请登录后再查询与「我」相关的走访任务。",
            template_id=None,
        )
    return plan


_COMPOUND_INTENT_TEMPLATES: dict[str, list[str]] = {
    "responsible_elderly_list": ["responsible_elderly_list", "responsible_elderly_list_member"],
}


def _merge_responsible_elderly_rows(row_sets: list[list[dict]]) -> list[dict]:
    merged: dict[str, dict] = {}
    for rows in row_sets:
        for row in rows:
            row_id = str(row.get("id") or "")
            if not row_id:
                continue
            if row_id not in merged:
                merged[row_id] = dict(row)
                continue
            existing = merged[row_id]
            roles = {
                str(existing.get("responsibility_role") or "").strip(),
                str(row.get("responsibility_role") or "").strip(),
            }
            roles.discard("")
            if len(roles) > 1:
                existing["responsibility_role"] = "网格管理员/成员"
            grid_names = {
                str(existing.get("grid_name") or "").strip(),
                str(row.get("grid_name") or "").strip(),
            }
            grid_names.discard("")
            if len(grid_names) > 1:
                existing["grid_name"] = "、".join(sorted(grid_names))
    return sorted(merged.values(), key=lambda item: str(item.get("name") or ""))


def _answer_visiting_self_rank(rows: list[dict], current_user_id: str | None) -> str:
    if not current_user_id:
        return "请登录后再查询与「我」相关的走访任务。"
    if not rows:
        return "暂无走访排名数据。"
    ranked = sorted(
        rows,
        key=lambda row: (-int(row.get("total") or 0), str(row.get("visit_person_id") or "")),
    )
    rank = 1
    for index, row in enumerate(ranked):
        if index > 0 and int(row.get("total") or 0) < int(ranked[index - 1].get("total") or 0):
            rank = index + 1
        if str(row.get("visit_person_id") or "") == current_user_id:
            total = int(row.get("total") or 0)
            if total <= 0:
                return "您暂无走访记录，尚未进入排名。"
            return f"您的走访量排在第 {rank} 名（共 {total} 次）。"
    return "未找到您的走访记录，尚未进入排名。"


class Text2SqlService:
    def __init__(
        self,
        settings: RuntimeSettings,
        catalog: SchemaCatalog,
        semantics: SemanticIndex,
        business_semantics: BusinessSemanticIndex | None = None,
        executor: SqlExecutor | None = None,
        audit_store: SQLiteAuditStore | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.semantics = semantics
        self.business_semantics = business_semantics or BusinessSemanticIndex.from_config(
            settings.project_root / "configs" / "business_semantics.yaml",
            vector_settings=settings.intent_vector,
            llm_settings=settings.llm,
        )
        self.router = QueryRouter(
            catalog,
            semantics,
            settings.performance,
            allow_sensitive_fields=settings.allow_sensitive_fields,
        )
        self.context_builder = SchemaContextBuilder(
            catalog,
            semantics,
            allow_sensitive_fields=settings.allow_sensitive_fields,
        )
        fallback = SchemaDrivenSqlGenerator(
            catalog,
            semantics,
            allow_sensitive_fields=settings.allow_sensitive_fields,
        )
        self.schema_generator = fallback
        self.generator = OpenAICompatibleSqlGenerator(settings.llm, fallback)
        allowed_functions = settings.performance.get("allowed_functions", [])
        self.guard = SqlGuard(
            catalog,
            allowed_functions=allowed_functions,
            allow_sensitive_fields=settings.allow_sensitive_fields,
        )
        self.executor = executor or build_executor(settings)
        self.audit_store = audit_store or SQLiteAuditStore(settings.audit_db_path)
        entity_labels = EntityColumnLabelIndex.from_business_semantics_path(
            settings.project_root / "configs" / "business_semantics.yaml",
        )
        self.formatter = ResultFormatter(catalog, entity_labels)

    @classmethod
    def from_project_root(cls, project_root: Path | None = None) -> "Text2SqlService":
        settings = load_settings(project_root)
        catalog = SchemaCatalog.from_whitelist(settings.project_root / "configs" / "whitelist_tables.yaml")
        semantics = SemanticIndex.from_config(settings.project_root / "configs" / "semantic_overrides.yaml")
        business_semantics = BusinessSemanticIndex.from_config(
            settings.project_root / "configs" / "business_semantics.yaml",
            vector_settings=settings.intent_vector,
            llm_settings=settings.llm,
        )
        return cls(
            settings=settings,
            catalog=catalog,
            semantics=semantics,
            business_semantics=business_semantics,
        )

    def estimate(
        self,
        question: str,
        domain_id: str | None,
        history: list[dict[str, object]] | None = None,
    ) -> EstimateResult:
        semantic_plan: SemanticPlan | None = None
        try:
            effective_question, _ = contextualize_question(question, history)
            semantic_plan = self.business_semantics.plan(effective_question, history)
            executable_statuses = _EXECUTABLE_STATUSES
            self.router.validate_question_policies(
                effective_question,
                domain_id,
                include_sensitive=semantic_plan.status in executable_statuses,
            )
            if semantic_plan.status == "metadata":
                return EstimateResult(
                    status="planned",
                    hit_path="field_explanation",
                    estimated_seconds=0,
                    candidate_tables=[],
                    semantic_plan=semantic_plan.to_dict(),
                )
            if semantic_plan.status not in executable_statuses:
                return EstimateResult(
                    status="rejected",
                    hit_path=f"semantic_{semantic_plan.status}",
                    estimated_seconds=0,
                    candidate_tables=semantic_plan.candidate_tables,
                    rejection_reason=semantic_plan.reason,
                    semantic_plan=semantic_plan.to_dict(),
                )
            estimate = self.router.estimate_tables(
                effective_question,
                domain_id,
                semantic_plan.candidate_tables,
                self._semantic_hit_path(semantic_plan),
            )
            return type(estimate)(
                status=estimate.status,
                hit_path=estimate.hit_path,
                estimated_seconds=estimate.estimated_seconds,
                candidate_tables=estimate.candidate_tables,
                rejection_reason=estimate.rejection_reason,
                warnings=estimate.warnings,
                semantic_plan=semantic_plan.to_dict(),
            )
        except RejectedQuery as exc:
            return EstimateResult(
                status="rejected",
                hit_path="rejected",
                estimated_seconds=0,
                candidate_tables=[],
                rejection_reason=exc.reason,
                semantic_plan=semantic_plan.to_dict() if semantic_plan else None,
            )

    def query(self, query_input: QueryInput) -> QueryResult:
        query_id = str(uuid.uuid4())
        start = time.monotonic()
        sql_for_audit: str | None = None
        warnings: list[str] = []
        interaction_logs: list[dict[str, object]] = []
        semantic_plan: SemanticPlan | None = None
        try:
            effective_question, conversation_log = contextualize_question(
                query_input.question,
                query_input.history,
            )
            if conversation_log:
                interaction_logs.append(conversation_log)
            semantic_plan = self.business_semantics.plan(effective_question, query_input.history)
            semantic_plan = _apply_query_actor(semantic_plan, query_input, effective_question)
            interaction_logs.append(self._semantic_log(semantic_plan))
            executable_statuses = _EXECUTABLE_STATUSES
            self.router.validate_question_policies(
                effective_question,
                query_input.domain_id,
                include_sensitive=semantic_plan.status in executable_statuses,
            )
            if semantic_plan.status == "metadata":
                if semantic_plan.intent != "field_explanation":
                    raise RejectedQuery(
                        semantic_plan.reason or "当前 metadata 意图暂不支持",
                        semantic_plan.status,
                    )
                resolved = resolve_field(
                    effective_question,
                    semantic_plan.slots,
                    self.catalog,
                    self.settings.project_root,
                )
                if resolved is None:
                    raise RejectedQuery(
                        "未在白名单 schema 中找到该字段，请改用物理字段名如 resident.residence_status。",
                        "field_not_found",
                    )
                answer, table = explain_field(resolved)
                elapsed_ms = int((time.monotonic() - start) * 1000)
                result = QueryResult(
                    query_id=query_id,
                    status="ok",
                    answer=answer,
                    table=table,
                    generated_sql=None,
                    elapsed_ms=elapsed_ms,
                    hit_path="field_explanation",
                    explain=[],
                    warnings=warnings,
                    interaction_logs=interaction_logs,
                    semantic_plan=semantic_plan.to_dict(),
                )
                self._audit(query_id, query_input, result, None, 0)
                return result
            if semantic_plan.status not in executable_statuses:
                raise RejectedQuery(
                    semantic_plan.reason or "当前语义意图不可执行",
                    semantic_plan.status,
                )
            estimate = self.router.estimate_tables(
                effective_question,
                query_input.domain_id,
                semantic_plan.candidate_tables,
                self._semantic_hit_path(semantic_plan),
            )
            warnings.extend(estimate.warnings)
            if semantic_plan.status == "guarded_text2sql":
                schema_context = self.context_builder.build(
                    effective_question,
                    estimate.candidate_tables,
                    query_input.history,
                )
                generator = (
                    self.generator
                    if self._should_use_llm(estimate, force_llm=query_input.force_llm)
                    else self.schema_generator
                )
                generated = generator.generate(
                    effective_question,
                    schema_context,
                    estimate.candidate_tables,
                )
            else:
                if semantic_plan.intent in _COMPOUND_INTENT_TEMPLATES:
                    generated = GeneratedSql(
                        sql="",
                        plan="compound semantic query",
                        hit_path="semantic_template",
                        params={},
                        interaction_logs=[],
                    )
                else:
                    generated = self.business_semantics.compile(semantic_plan)
            interaction_logs.extend(generated.interaction_logs)

            limits = self.settings.performance.get("limits", {})
            default_limit = int(limits.get("default_detail_limit", 200))
            max_limit = int(query_input.max_rows or limits.get("max_detail_limit", 1000))
            scanned_rows = 0
            if semantic_plan.intent in _COMPOUND_INTENT_TEMPLATES:
                execution, guard_result, generated, scanned_rows = self._execute_compound_semantic_query(
                    semantic_plan,
                    query_input.domain_id,
                    default_limit,
                    max_limit,
                )
                interaction_logs.extend(generated.interaction_logs)
                sql_for_audit = guard_result.sql
            else:
                sql_with_domain, domain_params = inject_domain_filter(
                    generated.sql,
                    self.catalog,
                    query_input.domain_id,
                )
                params = {**generated.params, **domain_params}
                guarded_sql = ensure_limit(sql_with_domain, default_limit, max_limit)
                guard_result = self.guard.validate(guarded_sql)
                sql_for_audit = guard_result.sql
                warnings.extend(guard_result.warnings)

                self.router.reject_if_sql_too_complex(guard_result.tables)
                explain, scanned_rows = self.executor.explain(guard_result.sql, params)
                self.router.reject_if_explain_too_large(scanned_rows)
                execution = self.executor.execute(guard_result.sql, params, max_limit)
                if semantic_plan.intent == "grid_building_list":
                    execution, guard_result, sql_for_audit = self._finalize_grid_building_list_execution(
                        semantic_plan,
                        query_input.domain_id,
                        execution,
                        guard_result,
                        sql_for_audit,
                        default_limit,
                        max_limit,
                    )
                if not execution.explain:
                    execution = type(execution)(
                        columns=execution.columns,
                        rows=execution.rows,
                        explain=explain,
                        elapsed_ms=execution.elapsed_ms,
                        scanned_rows=scanned_rows,
                        mode=execution.mode,
                    )
            answer, table = self.formatter.format(
                execution,
                guard_result.sql,
                guard_result.tables,
            )
            if semantic_plan.intent == "visiting_self_rank":
                answer = _answer_visiting_self_rank(
                    execution.rows,
                    semantic_plan.slots.get("current_user_id"),
                )
            elif semantic_plan.intent == "grid_building_list" and execution.rows:
                answer = _answer_grid_building_list(execution.rows)
            elif semantic_plan.intent == "grid_building_list" and not execution.rows:
                grid_label = str(semantic_plan.slots.get("grid_name") or "该网格")
                if execution.mode == "dry_run":
                    answer = f"未找到「{grid_label}」下的楼栋列表。"
                else:
                    matched = self._lookup_grid_names(
                        query_input.domain_id,
                        str(semantic_plan.slots.get("grid_name") or ""),
                        str(semantic_plan.slots.get("grid_name_like") or f"%{grid_label}%"),
                    )
                    if matched:
                        answer = (
                            f"已找到网格「{'、'.join(matched)}」，但该网格暂未关联网格内楼栋，"
                            "请在网格管理中维护房屋范围后再查询。"
                        )
                    else:
                        answer = f"未找到名为「{grid_label}」的网格，请确认网格名称是否正确。"
            elif semantic_plan.intent == "grid_party_member_distribution" and not execution.rows:
                grid_label = str(semantic_plan.slots.get("grid_name") or "该网格")
                answer = (
                    f"「{grid_label}」下尚未关联房屋节点，或该网格暂无登记党员，"
                    "无法生成楼栋分布热力图。"
                )
            echarts_option = None
            chart_answer, chart_option = maybe_build_chart(
                semantic_plan.intent,
                execution.rows,
                semantic_plan.slots,
                question=query_input.question,
            )
            if chart_option is None and execution.rows:
                chart_answer, chart_option = maybe_build_generic_distribution_chart(
                    query_input.question,
                    execution.rows,
                    semantic_plan.slots,
                    semantic_plan.intent,
                )
            if chart_option is not None and chart_answer is not None:
                echarts_option = chart_option
                answer = append_echarts_fence(chart_answer, chart_option)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status = "planned" if execution.mode == "dry_run" else "ok"
            result = QueryResult(
                query_id=query_id,
                status=status,
                answer=answer,
                table=table,
                generated_sql=guard_result.sql if query_input.allow_return_sql else None,
                elapsed_ms=elapsed_ms,
                hit_path=generated.hit_path,
                explain=execution.explain,
                warnings=warnings,
                interaction_logs=interaction_logs,
                semantic_plan=semantic_plan.to_dict(),
                echarts_option=echarts_option,
            )
            self._audit(query_id, query_input, result, sql_for_audit, scanned_rows)
            return result
        except RejectedQuery as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = QueryResult(
                query_id=query_id,
                status="rejected",
                answer=None,
                table=None,
                generated_sql=sql_for_audit if query_input.allow_return_sql else None,
                elapsed_ms=elapsed_ms,
                hit_path="rejected",
                rejection_reason=exc.reason,
                warnings=warnings,
                interaction_logs=interaction_logs,
                semantic_plan=semantic_plan.to_dict() if semantic_plan else None,
            )
            self._audit(query_id, query_input, result, sql_for_audit, 0)
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            result = QueryResult(
                query_id=query_id,
                status="failed",
                answer=None,
                table=None,
                generated_sql=sql_for_audit if query_input.allow_return_sql else None,
                elapsed_ms=elapsed_ms,
                hit_path="failed",
                rejection_reason=str(exc),
                warnings=warnings,
                interaction_logs=interaction_logs,
                semantic_plan=semantic_plan.to_dict() if semantic_plan else None,
            )
            self._audit(query_id, query_input, result, sql_for_audit, 0)
            return result

    def audit(self, query_id: str):
        return self.audit_store.get(query_id)

    def unsupported_questions(self, *, limit: int = 50, since_ms: int | None = None):
        return self.audit_store.unsupported_questions(limit=limit, since_ms=since_ms)

    def schema_summary(self):
        summary = self.catalog.summary()
        summary["execution_mode"] = "live" if self.settings.live_execution else "dry_run"
        summary["executor_backend"] = self.settings.executor_backend
        summary["allow_sensitive_fields"] = self.settings.allow_sensitive_fields
        summary["semantic_concepts"] = [
            {"name": concept.name, "display_name": concept.display_name, "keywords": concept.keywords}
            for concept in self.semantics.concepts.values()
        ]
        summary["business_intents"] = self.business_semantics.summary()
        return summary

    def _should_use_llm(self, estimate: EstimateResult, *, force_llm: bool = False) -> bool:
        if not self.settings.llm.configured:
            return False
        if force_llm:
            return True
        policy = self.settings.llm.policy
        if policy == "off":
            return False
        if policy == "always":
            return True
        return estimate.hit_path == "guarded_text2sql" and len(estimate.candidate_tables) > 1

    def _finalize_grid_building_list_execution(
        self,
        plan: SemanticPlan,
        domain_id: str,
        execution: ExecutionResult,
        guard_result: GuardResult,
        sql_for_audit: str,
        default_limit: int,
        max_limit: int,
    ) -> tuple[ExecutionResult, GuardResult, str]:
        rows = list(execution.rows)
        if rows:
            level3_rows = [row for row in rows if row.get("node_level") == 3]
            if level3_rows:
                rows = level3_rows
            return self._grid_building_list_execution(rows, execution), guard_result, sql_for_audit

        if execution.mode == "dry_run":
            return self._grid_building_list_execution(rows, execution), guard_result, sql_for_audit

        fallback_plan = replace(plan, template_id="grid_building_list_house_fallback")
        generated = self.business_semantics.compile(fallback_plan)
        sql_with_domain, domain_params = inject_domain_filter(
            generated.sql,
            self.catalog,
            domain_id,
        )
        params = {**generated.params, **domain_params}
        guarded_sql = ensure_limit(sql_with_domain, default_limit, max_limit)
        fallback_guard = self.guard.validate(guarded_sql)
        self.router.reject_if_sql_too_complex(fallback_guard.tables)
        fallback_execution = self.executor.execute(fallback_guard.sql, params, max_limit)
        return (
            self._grid_building_list_execution(fallback_execution.rows, fallback_execution),
            fallback_guard,
            fallback_guard.sql,
        )

    def _grid_building_list_execution(
        self,
        rows: list[dict[str, object]],
        execution: ExecutionResult,
    ) -> ExecutionResult:
        cleaned_rows = [
            {key: value for key, value in row.items() if key != "node_level"}
            for row in rows
        ]
        columns = [column for column in execution.columns if column != "node_level"]
        return type(execution)(
            columns=columns,
            rows=cleaned_rows,
            explain=execution.explain,
            elapsed_ms=execution.elapsed_ms,
            scanned_rows=execution.scanned_rows,
            mode=execution.mode,
        )

    def _lookup_grid_names(self, domain_id: str, grid_name: str, grid_name_like: str) -> list[str]:
        sql = """
        SELECT name
        FROM community_grid
        WHERE domain_id = %(domain_id)s
          AND (name = %(grid_name)s OR name LIKE %(grid_name_like)s)
        ORDER BY name
        LIMIT 5
        """
        params = {
            "domain_id": domain_id,
            "grid_name": grid_name,
            "grid_name_like": grid_name_like or f"%{grid_name}%",
        }
        try:
            execution = self.executor.execute(sql, params, max_rows=5)
        except Exception:
            return []
        return [
            str(row.get("name") or "").strip()
            for row in execution.rows
            if str(row.get("name") or "").strip()
        ]

    def _audit(
        self,
        query_id: str,
        query_input: QueryInput,
        result: QueryResult,
        sql: str | None,
        scanned_rows: int,
    ) -> None:
        self.audit_store.record(
            {
                "query_id": query_id,
                "user_id": query_input.user_id,
                "domain_id": query_input.domain_id,
                "question": query_input.question,
                "status": result.status,
                "hit_path": result.hit_path,
                "sql": sql,
                "rejection_reason": result.rejection_reason,
                "elapsed_ms": result.elapsed_ms,
                "scanned_rows": scanned_rows,
                "explain": result.explain,
                "result": result.table or {},
                "warnings": result.warnings,
                "interaction_logs": result.interaction_logs,
            }
        )

    def _semantic_log(self, semantic_plan: SemanticPlan) -> dict[str, object]:
        return {
            "kind": "semantic_planner",
            "status": semantic_plan.status,
            "intent": semantic_plan.intent,
            "displayName": semantic_plan.display_name,
            "templateId": semantic_plan.template_id,
            "slots": semantic_plan.slots,
            "missingSlots": semantic_plan.missing_slots,
            "candidateTables": semantic_plan.candidate_tables,
            "reason": semantic_plan.reason,
            "needs": semantic_plan.needs,
            "elapsedMs": semantic_plan.elapsed_ms,
            "candidateIntents": semantic_plan.candidate_intents,
            "matchedQuery": semantic_plan.matched_query,
            "vectorDistance": semantic_plan.vector_distance,
            "slotSource": semantic_plan.slot_source,
            "slotElapsedMs": semantic_plan.slot_elapsed_ms,
        }

    def _execute_compound_semantic_query(
        self,
        semantic_plan: SemanticPlan,
        domain_id: str,
        default_limit: int,
        max_limit: int,
    ) -> tuple[ExecutionResult, GuardResult, GeneratedSql, int]:
        template_ids = _COMPOUND_INTENT_TEMPLATES[semantic_plan.intent]
        row_sets: list[list[dict]] = []
        columns: list[str] = []
        explain_rows: list[dict] = []
        scanned_rows = 0
        elapsed_ms = 0
        mode = "dry_run"
        interaction_logs: list[dict[str, object]] = []
        guarded_sql_parts: list[str] = []

        for template_id in template_ids:
            generated = self.business_semantics.compile(replace(semantic_plan, template_id=template_id))
            interaction_logs.extend(generated.interaction_logs)
            sql_with_domain, domain_params = inject_domain_filter(
                generated.sql,
                self.catalog,
                domain_id,
            )
            params = {**generated.params, **domain_params}
            guarded_sql = ensure_limit(sql_with_domain, default_limit, max_limit)
            guard_result = self.guard.validate(guarded_sql)
            guarded_sql_parts.append(guard_result.sql)
            self.router.reject_if_sql_too_complex(guard_result.tables)
            explain, part_scanned_rows = self.executor.explain(guard_result.sql, params)
            scanned_rows += part_scanned_rows
            explain_rows.extend(explain)
            part_execution = self.executor.execute(guard_result.sql, params, max_limit)
            elapsed_ms += part_execution.elapsed_ms
            mode = part_execution.mode
            if part_execution.columns and not columns:
                columns = list(part_execution.columns)
            row_sets.append(list(part_execution.rows))

        self.router.reject_if_explain_too_large(scanned_rows)
        merged_rows = _merge_responsible_elderly_rows(row_sets)
        execution = ExecutionResult(
            columns=columns,
            rows=merged_rows,
            explain=explain_rows,
            elapsed_ms=elapsed_ms,
            scanned_rows=scanned_rows,
            mode=mode,
        )
        combined_sql = " UNION ".join(guarded_sql_parts)
        combined_guard = GuardResult(sql=combined_sql, tables=[], warnings=[])
        combined_generated = GeneratedSql(
            sql=combined_sql,
            plan="compound semantic query",
            hit_path="semantic_template",
            params=generated.params,
            interaction_logs=interaction_logs,
        )
        return execution, combined_guard, combined_generated, scanned_rows

    def _semantic_hit_path(self, semantic_plan: SemanticPlan) -> str:
        if semantic_plan.status == "guarded_text2sql":
            return "guarded_text2sql"
        return "semantic_template"
