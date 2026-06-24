from __future__ import annotations

import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audit import SQLiteAuditStore
from .business_semantics import BusinessSemanticIndex, SemanticPlan, resolve_business_semantics_path
from .config import IntentRoutingSettings, RuntimeSettings, load_settings
from .context import SchemaContextBuilder
from .conversation import contextualize_question
from .executor import SqlExecutor, build_executor
from .field_encryption import encrypt_sensitive_query_params
from .field_explanation import explain_field, resolve_field
from .column_labels import EntityColumnLabelIndex
from .formatter import ResultFormatter
from .generator import OpenAICompatibleSqlGenerator, SchemaDrivenSqlGenerator
from .memory import MemoryRecord, SQLiteMemoryStore
from .models import EstimateResult, ExecutionResult, QueryInput, QueryResult, RejectedQuery
from .router import QueryRouter
from .schema import SchemaCatalog
from .semantic_enrichment import SemanticEnrichmentIndex
from .semantics import SemanticIndex
from .sql_guard import SqlGuard
from .sql_policy import ensure_limit, inject_domain_filter
from .visualization import append_echarts_fence, maybe_build_chart, maybe_build_generic_distribution_chart

_EXECUTABLE_STATUSES = frozenset({"executable", "guarded_text2sql", "metadata"})


def _apply_query_actor(plan: SemanticPlan, query_input: QueryInput, question: str) -> SemanticPlan:
    if not query_input.user_id or "我" not in question:
        return plan
    slots = dict(plan.slots)
    slots["current_user_id"] = query_input.user_id
    return replace(plan, slots=slots)


class Text2SqlService:
    def __init__(
        self,
        settings: RuntimeSettings,
        catalog: SchemaCatalog,
        semantics: SemanticIndex,
        business_semantics: BusinessSemanticIndex | None = None,
        executor: SqlExecutor | None = None,
        audit_store: SQLiteAuditStore | None = None,
        memory_store: SQLiteMemoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.semantics = semantics
        self.business_semantics = business_semantics or BusinessSemanticIndex.from_config(
            resolve_business_semantics_path(settings.project_root),
            vector_settings=settings.intent_vector,
            llm_settings=settings.llm,
            routing_settings=IntentRoutingSettings.from_performance(settings.performance),
            field_encryption=settings.field_encryption,
        )
        self.router = QueryRouter(
            catalog,
            semantics,
            settings.performance,
            allow_sensitive_fields=settings.allow_sensitive_fields,
        )
        self.enrichment = SemanticEnrichmentIndex.from_config(
            settings.project_root / "configs" / "entity_enrichment.yaml",
        )
        self.context_builder = SchemaContextBuilder(
            catalog,
            semantics,
            allow_sensitive_fields=settings.allow_sensitive_fields,
            enrichment=self.enrichment,
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
        self.memory_store = memory_store or SQLiteMemoryStore(settings.audit_db_path)
        entity_labels = EntityColumnLabelIndex.from_business_semantics_path(
            resolve_business_semantics_path(settings.project_root),
        )
        self.formatter = ResultFormatter(catalog, entity_labels)

    @classmethod
    def from_project_root(cls, project_root: Path | None = None) -> "Text2SqlService":
        settings = load_settings(project_root)
        catalog = SchemaCatalog.from_whitelist(settings.project_root / "configs" / "whitelist_tables.yaml")
        semantics = SemanticIndex.from_config(settings.project_root / "configs" / "semantic_overrides.yaml")
        business_semantics = BusinessSemanticIndex.from_config(
            resolve_business_semantics_path(settings.project_root),
            vector_settings=settings.intent_vector,
            llm_settings=settings.llm,
            routing_settings=IntentRoutingSettings.from_performance(settings.performance),
            field_encryption=settings.field_encryption,
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
        applied_memories: list[MemoryRecord] = []
        try:
            effective_question, conversation_log = contextualize_question(
                query_input.question,
                query_input.history,
            )
            if conversation_log:
                interaction_logs.append(conversation_log)
            applied_memories = self._retrieve_memories(
                effective_question,
                domain_id=query_input.domain_id,
                user_id=query_input.user_id,
            )
            if applied_memories:
                interaction_logs.append(self._memory_log(applied_memories))
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
                    enrichment=self.enrichment,
                )
                if resolved is None:
                    raise RejectedQuery(
                        "未在白名单 schema 中找到该字段，请改用物理字段名如 payment_order.status。",
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
                    applied_memories=self._memory_payload(applied_memories),
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
                    memories=applied_memories,
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
                generated = self.business_semantics.compile(semantic_plan)
            interaction_logs.extend(generated.interaction_logs)

            limits = self.settings.performance.get("limits", {})
            default_limit = int(limits.get("default_detail_limit", 200))
            max_limit = int(query_input.max_rows or limits.get("max_detail_limit", 1000))
            scanned_rows = 0
            execution_params: dict[str, Any] | None = None
            sql_with_domain, domain_params = inject_domain_filter(
                generated.sql,
                self.catalog,
                query_input.domain_id,
            )
            params = {**generated.params, **domain_params}
            params = encrypt_sensitive_query_params(
                params,
                intent_id=semantic_plan.intent or "",
                settings=self.settings.field_encryption,
            )
            execution_params = dict(params)
            guarded_sql = ensure_limit(sql_with_domain, default_limit, max_limit)
            guard_result = self.guard.validate(guarded_sql)
            sql_for_audit = guard_result.sql
            warnings.extend(guard_result.warnings)

            self.router.reject_if_sql_too_complex(guard_result.tables)
            explain, scanned_rows = self.executor.explain(guard_result.sql, params)
            self.router.reject_if_explain_too_large(scanned_rows)
            execution = self.executor.execute(guard_result.sql, params, max_limit)
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
                question=effective_question,
                intent_id=semantic_plan.intent,
                display_name=semantic_plan.display_name,
                output_type=semantic_plan.output_type,
            )
            echarts_option = None
            chart_answer, chart_option = maybe_build_chart(
                semantic_plan.intent,
                execution.rows,
                semantic_plan.slots,
                question=effective_question,
            )
            if chart_option is None and execution.rows:
                chart_answer, chart_option = maybe_build_generic_distribution_chart(
                    effective_question,
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
                execution_rows=[dict(row) for row in execution.rows],
                execution_params=execution_params,
                applied_memories=self._memory_payload(applied_memories),
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
                applied_memories=self._memory_payload(applied_memories),
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
                applied_memories=self._memory_payload(applied_memories),
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

    def confirm_memory(
        self,
        *,
        content: str,
        scope: str,
        kind: str = "correction",
        title: str | None = None,
        domain_id: str | None = None,
        user_id: str | None = None,
        keywords: list[str] | None = None,
        source_query_id: str | None = None,
        confirmed_by: str | None = None,
        replace_memory_ids: list[str] | None = None,
        allow_conflict: bool = False,
    ) -> dict[str, Any]:
        duplicate, conflicts, _ = self.memory_store.prepare_create(
            content=content,
            scope=scope,
            kind=kind,
            title=title,
            domain_id=domain_id,
            user_id=user_id,
            keywords=keywords,
        )
        if duplicate is not None:
            return {
                "status": "exists",
                "memory": duplicate.to_dict(),
                "conflicts": [],
                "replacedMemoryIds": [],
            }
        if conflicts and not allow_conflict and not replace_memory_ids:
            return {
                "status": "conflict",
                "memory": None,
                "conflicts": [item.to_dict() for item in conflicts],
                "replacedMemoryIds": [],
            }
        record = self.memory_store.create(
            content=content,
            scope=scope,
            kind=kind,
            title=title,
            domain_id=domain_id,
            user_id=user_id,
            keywords=keywords,
            source_query_id=source_query_id,
            confirmed_by=confirmed_by,
            replace_memory_ids=replace_memory_ids,
            allow_conflict=allow_conflict,
        )
        return {
            "status": "created",
            "memory": record.to_dict(),
            "conflicts": [item.to_dict() for item in conflicts],
            "replacedMemoryIds": list(replace_memory_ids or []),
        }

    def list_memories(
        self,
        *,
        domain_id: str | None = None,
        user_id: str | None = None,
        scope: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        items = self.memory_store.list_memories(
            domain_id=domain_id,
            user_id=user_id,
            scope=scope,
            limit=limit,
        )
        return {
            "items": [item.to_dict() for item in items],
            "count": len(items),
        }

    def deactivate_memory(self, memory_id: str) -> bool:
        return self.memory_store.deactivate(memory_id)

    def _memory_top_k(self) -> int:
        memory_settings = self.settings.performance.get("memory")
        if not isinstance(memory_settings, dict):
            return 3
        return max(1, min(int(memory_settings.get("top_k", 3)), 10))

    def _retrieve_memories(
        self,
        question: str,
        *,
        domain_id: str | None,
        user_id: str | None,
    ) -> list[MemoryRecord]:
        memory_settings = self.settings.performance.get("memory")
        min_score = 1.0
        if isinstance(memory_settings, dict) and memory_settings.get("min_score") is not None:
            min_score = float(memory_settings["min_score"])
        return self.memory_store.retrieve(
            question=question,
            domain_id=domain_id,
            user_id=user_id,
            limit=self._memory_top_k(),
            min_score=min_score,
        )

    @staticmethod
    def _memory_payload(memories: list[MemoryRecord]) -> list[dict[str, Any]] | None:
        if not memories:
            return None
        return [item.to_dict() for item in memories]

    @staticmethod
    def _memory_log(memories: list[MemoryRecord]) -> dict[str, Any]:
        return {
            "kind": "memory",
            "status": "applied",
            "count": len(memories),
            "items": [item.to_dict() for item in memories],
        }

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

    def _semantic_hit_path(self, semantic_plan: SemanticPlan) -> str:
        if semantic_plan.status == "guarded_text2sql":
            return "guarded_text2sql"
        return "semantic_template"
