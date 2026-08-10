from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entity_query import EntityQueryCompiler
from .intent_vector import IntentVectorCandidate, IntentVectorIndex, build_intent_vector_index
from .models import GeneratedSql, RejectedQuery
from .rejection_reasons import UNCONFIGURED_SEMANTIC_REASON
from .semantic_slot_extractor import LlmSlotExtractor, SlotExtractionResult
from .semantic_slots import computed_values, derive_slots, extract_slots
from .config import FieldEncryptionSettings, IntentRoutingSettings, IntentVectorSettings, LlmSettings, load_yaml
from .field_encryption import encrypt_sensitive_query_params

PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
OPTIONAL_BLOCK_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(.*?)\]\]", re.DOTALL)


@dataclass(frozen=True)
class BusinessIntent:
    intent_id: str
    display_name: str
    status: str
    priority: int
    match_any: tuple[str, ...] = ()
    match_all: tuple[str, ...] = ()
    match_none: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    semantic_queries: tuple[str, ...] = ()
    semantic_negative_queries: tuple[str, ...] = ()
    semantic_boundary_queries: tuple[str, ...] = ()
    semantic_boundary_negative_queries: tuple[str, ...] = ()
    ontology_refs: tuple[str, ...] = ()
    required_slots: tuple[str, ...] = ()
    optional_slots: tuple[str, ...] = ()
    physical_tables: tuple[str, ...] = ()
    output_type: str | None = None
    template_id: str | None = None
    reason: str | None = None
    remark: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()
    slot_defaults: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SqlTemplate:
    template_id: str
    sql: str
    plan: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticPlan:
    status: str
    intent: str | None = None
    display_name: str | None = None
    output_type: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    missing_slots: list[str] = field(default_factory=list)
    candidate_tables: list[str] = field(default_factory=list)
    template_id: str | None = None
    ontology_refs: list[str] = field(default_factory=list)
    reason: str | None = None
    needs: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    candidate_intents: list[dict[str, Any]] = field(default_factory=list)
    matched_query: str | None = None
    vector_distance: float | None = None
    slot_source: str | None = None
    slot_elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "intent": self.intent,
            "displayName": self.display_name,
            "outputType": self.output_type,
            "confidence": self.confidence,
            "slots": self.slots,
            "missingSlots": self.missing_slots,
            "candidateTables": self.candidate_tables,
            "templateId": self.template_id,
            "ontologyRefs": self.ontology_refs,
            "reason": self.reason,
            "needs": self.needs,
            "elapsedMs": self.elapsed_ms,
            "candidateIntents": self.candidate_intents,
            "matchedQuery": self.matched_query,
            "vectorDistance": self.vector_distance,
            "slotSource": self.slot_source,
            "slotElapsedMs": self.slot_elapsed_ms,
        }


def _load_business_semantics_config(path: Path) -> dict[str, Any]:
    """Load business semantics from a single YAML file or a directory of YAML files.

    When ``path`` is a directory, all ``*.yaml`` files within are loaded and merged:
    - ``entities``: shallow merge (later files override duplicate keys)
    - ``intents``: concatenation of all lists
    - ``sql_templates``: shallow merge (later files override duplicate keys)
    - ``entity_query_schemas``: shallow merge (later files override duplicate keys)
    """
    if path.is_file():
        return load_yaml(path)

    if not path.is_dir():
        return {}

    merged: dict[str, Any] = {
        "entities": {},
        "intents": [],
        "sql_templates": {},
        "entity_query_schemas": {},
    }

    for yaml_file in sorted(path.glob("*.yaml")):
        raw = load_yaml(yaml_file)
        if "entities" in raw:
            merged["entities"].update(raw["entities"])
        if "intents" in raw:
            merged["intents"].extend(raw["intents"])
        if "sql_templates" in raw:
            merged["sql_templates"].update(raw["sql_templates"])
        if "entity_query_schemas" in raw:
            merged["entity_query_schemas"].update(raw["entity_query_schemas"])

    return merged


def resolve_business_semantics_path(project_root: Path) -> Path:
    """Resolve the business semantics config path.

    Prefers ``configs/business_semantics/`` directory over the legacy single
    ``configs/business_semantics.yaml`` file for backward compatibility.
    """
    dir_path = project_root / "configs" / "business_semantics"
    if dir_path.exists():
        return dir_path
    return project_root / "configs" / "business_semantics.yaml"


class BusinessSemanticIndex:
    def __init__(
        self,
        entities: dict[str, Any],
        intents: list[BusinessIntent],
        templates: dict[str, SqlTemplate],
        vector_settings: IntentVectorSettings | None = None,
        llm_settings: LlmSettings | None = None,
        slot_extractor: LlmSlotExtractor | None = None,
        vector_index: IntentVectorIndex | None = None,
        entity_query_compiler: EntityQueryCompiler | None = None,
        llm_slot_policy: str | None = None,
        routing_settings: IntentRoutingSettings | None = None,
        field_encryption: FieldEncryptionSettings | None = None,
    ) -> None:
        self.entities = entities
        self.intents = intents
        self.templates = templates
        self.field_encryption = field_encryption or FieldEncryptionSettings()
        self.entity_query_compiler = entity_query_compiler or EntityQueryCompiler({})
        self._intents_by_id = {intent.intent_id: intent for intent in intents}
        self.routing = routing_settings or IntentRoutingSettings()
        self.vector_index = vector_index or build_intent_vector_index(vector_settings)
        self.slot_extractor = slot_extractor or (LlmSlotExtractor(llm_settings) if llm_settings else None)
        self._llm_configured = bool(llm_settings and llm_settings.configured)
        policy = llm_slot_policy or (llm_settings.slot_policy if llm_settings else "auto")
        self._llm_slot_policy = str(policy).strip().lower()
        if self._llm_slot_policy not in {"auto", "always", "never"}:
            self._llm_slot_policy = "auto"
        self._refresh_vector_index()

    @classmethod
    def from_config(
        cls,
        path: Path,
        vector_settings: IntentVectorSettings | None = None,
        llm_settings: LlmSettings | None = None,
        slot_extractor: LlmSlotExtractor | None = None,
        vector_index: IntentVectorIndex | None = None,
        llm_slot_policy: str | None = None,
        routing_settings: IntentRoutingSettings | None = None,
        field_encryption: FieldEncryptionSettings | None = None,
    ) -> "BusinessSemanticIndex":
        if not path.exists():
            return cls(
                {},
                [],
                {},
                vector_settings=vector_settings,
                llm_settings=llm_settings,
                slot_extractor=slot_extractor,
                vector_index=vector_index,
                entity_query_compiler=EntityQueryCompiler({}),
                llm_slot_policy=llm_slot_policy,
                routing_settings=routing_settings,
                field_encryption=field_encryption,
            )
        raw = _load_business_semantics_config(path)
        performance_path = path.parent / "performance.yaml"
        routing = routing_settings or IntentRoutingSettings.from_performance(
            load_yaml(performance_path) if performance_path.exists() else {}
        )
        intents = [
            BusinessIntent(
                intent_id=str(item["id"]),
                display_name=str(item.get("display_name", item["id"])),
                status=str(item.get("status", "needs_mapping")),
                priority=int(item.get("priority", 0)),
                match_any=tuple(str(value) for value in item.get("match", {}).get("any", [])),
                match_all=tuple(str(value) for value in item.get("match", {}).get("all", [])),
                match_none=tuple(str(value) for value in item.get("match", {}).get("none", [])),
                examples=tuple(str(value) for value in item.get("examples", [])),
                semantic_queries=tuple(str(value) for value in item.get("semantic", {}).get("queries", [])),
                semantic_negative_queries=tuple(
                    str(value) for value in item.get("semantic", {}).get("negative_queries", [])
                ),
                semantic_boundary_queries=tuple(
                    str(value) for value in item.get("semantic", {}).get("boundary_queries", [])
                ),
                semantic_boundary_negative_queries=tuple(
                    str(value)
                    for value in item.get("semantic", {}).get("boundary_negative_queries", [])
                ),
                ontology_refs=tuple(str(value) for value in item.get("ontology_refs", [])),
                required_slots=tuple(str(value) for value in item.get("required_slots", [])),
                optional_slots=tuple(str(value) for value in item.get("optional_slots", [])),
                physical_tables=tuple(str(value) for value in item.get("physical_tables", [])),
                output_type=item.get("output_type"),
                template_id=item.get("template"),
                reason=item.get("reason"),
                remark=_as_str_tuple(item.get("remark")),
                needs=tuple(str(value) for value in item.get("needs", [])),
                slot_defaults=dict(item.get("slot_defaults", {})),
            )
            for item in raw.get("intents", [])
            if isinstance(item, dict) and item.get("id")
        ]
        templates = {
            template_id: SqlTemplate(
                template_id=template_id,
                sql=str(item["sql"]),
                plan=str(item.get("plan", template_id)),
                params={str(key): str(value) for key, value in item.get("params", {}).items()},
            )
            for template_id, item in raw.get("sql_templates", {}).items()
            if isinstance(item, dict) and item.get("sql")
        }
        return cls(
            dict(raw.get("entities", {})),
            intents,
            templates,
            vector_settings=vector_settings,
            llm_settings=llm_settings,
            slot_extractor=slot_extractor,
            vector_index=vector_index,
            entity_query_compiler=EntityQueryCompiler.from_config(raw.get("entity_query_schemas")),
            llm_slot_policy=llm_slot_policy,
            routing_settings=routing,
            field_encryption=field_encryption,
        )

    def plan(self, question: str, history: list[dict[str, object]] | None = None) -> SemanticPlan:
        started = time.monotonic()
        candidates = self._candidate_intents(question) # 向量命中的意图集合

        # vector / embeddings未启用，则走自定义模板逻辑
        if not candidates and not self.vector_index.enabled:
            matched = self._best_intent(question) # match命中结果
            if matched is not None: # 命中match
                intent, confidence = matched
                if not self._passes_lexical_only_gate(question, intent, confidence): # 是否可信度高，不高则返回未配置
                    return SemanticPlan(
                        status="unsupported",
                        reason=UNCONFIGURED_SEMANTIC_REASON,
                        elapsed_ms=self._elapsed_ms(started),
                        slot_source="legacy_keywords_rejected",
                    )
                # 已配置 LLM 时，用 LLM 抽取槽位（部门名 / 多插槽等无法靠启发式穷举的场景）
                if self._llm_configured and self._llm_slot_policy != "never":
                    llm_slots, llm_source, llm_elapsed = self._extract_slots_via_llm(
                        question, [intent], history, confidence
                    )
                    if llm_slots is not None:
                        return self._build_plan(
                            question,
                            intent,
                            llm_slots,
                            confidence,
                            started,
                            candidate_intents=[],
                            matched_query=None,
                            vector_distance=None,
                            slot_source=llm_source,
                            slot_elapsed_ms=llm_elapsed,
                        )
                # 兜底：纯启发式槽位
                slots = self._complete_slots(
                    question,
                    intent,
                    {},
                    use_heuristic=True,
                )
                return self._build_plan(
                    question,
                    intent,
                    slots,
                    confidence,
                    started,
                    candidate_intents=[],
                    matched_query=None,
                    vector_distance=None,
                    slot_source="legacy_keywords_vector_disabled",
                    slot_elapsed_ms=0,
                )

        if not candidates:
            return SemanticPlan(
                status="unsupported",
                reason=UNCONFIGURED_SEMANTIC_REASON,
                elapsed_ms=self._elapsed_ms(started),
                slot_source="vector_no_candidate" if self.vector_index.enabled else "legacy_no_candidate",
            )

        candidate_intents = [self._candidate_projection(candidate) for candidate in candidates]

        if self._llm_slot_policy != "always":
            fast_plan = self._try_fast_heuristic_plan(
                question,
                candidates,
                candidate_intents,
                started,
            )
            if fast_plan is not None:
                return fast_plan

        extraction: SlotExtractionResult | None
        if self._llm_slot_policy == "never":
            extraction = None
        else:
            extraction = self._extract_slots_with_llm(question, candidate_intents, history)
        if extraction and extraction.decision == "fallback":
            return SemanticPlan(
                status="unsupported",
                reason=extraction.reason or UNCONFIGURED_SEMANTIC_REASON,
                elapsed_ms=self._elapsed_ms(started),
                candidate_intents=candidate_intents,
                slot_source=extraction.source,
                slot_elapsed_ms=extraction.elapsed_ms,
            )

        intent: BusinessIntent
        selected_candidate: IntentVectorCandidate
        confidence: float
        slots: dict[str, Any]
        slot_source: str
        slot_elapsed_ms: int
        llm_selected = False
        llm_confidence = 0.0
        if extraction and extraction.intent_id and extraction.intent_id in self._intents_by_id:
            intent = self._intents_by_id[extraction.intent_id]
            selected_candidate = self._candidate_for_intent(candidates, intent.intent_id) or candidates[0]
            slots = self._complete_slots(
                question,
                intent,
                extraction.slots,
                use_heuristic=False,
            )
            llm_confidence = float(extraction.confidence or 0.0)
            confidence = extraction.confidence or _confidence_from_distance(selected_candidate.distance)
            slot_source = extraction.source
            slot_elapsed_ms = extraction.elapsed_ms
            llm_selected = extraction.decision == "select"
        else:
            selected_candidate = candidates[0]
            intent = self._intents_by_id[selected_candidate.intent_id]
            if self.routing.require_high_confidence_without_llm and not self._passes_executable_routing_gate(
                question,
                intent,
                selected_candidate,
                candidates,
            ):
                return SemanticPlan(
                    status="unsupported",
                    reason=UNCONFIGURED_SEMANTIC_REASON,
                    elapsed_ms=self._elapsed_ms(started),
                    candidate_intents=candidate_intents,
                    slot_source="heuristic_low_confidence",
                )
            slots = self._complete_slots(
                question,
                intent,
                {},
                use_heuristic=True,
            )
            confidence = _confidence_from_distance(selected_candidate.distance)
            slot_source = "heuristic_llm_unavailable" if self._llm_configured else "heuristic_llm_unconfigured"
            slot_elapsed_ms = 0

        intent, selected_candidate, slots, confidence, slot_source = self._apply_strong_lexical_override(
            question,
            intent,
            selected_candidate,
            slots,
            confidence,
            slot_source,
            extraction.slots if extraction else {},
        )

        if intent.status == "executable" and llm_selected:
            if llm_confidence < self.routing.min_llm_select_confidence and not self._passes_executable_routing_gate(
                question,
                intent,
                selected_candidate,
                candidates,
            ):
                return SemanticPlan(
                    status="unsupported",
                    reason=UNCONFIGURED_SEMANTIC_REASON,
                    elapsed_ms=self._elapsed_ms(started),
                    candidate_intents=candidate_intents,
                    slot_source=f"{slot_source}_llm_low_confidence",
                    slot_elapsed_ms=slot_elapsed_ms,
                )
        elif intent.status == "executable" and not llm_selected and not self._passes_executable_routing_gate(
            question,
            intent,
            selected_candidate,
            candidates,
        ):
            return SemanticPlan(
                status="unsupported",
                reason=UNCONFIGURED_SEMANTIC_REASON,
                elapsed_ms=self._elapsed_ms(started),
                candidate_intents=candidate_intents,
                slot_source=f"{slot_source}_routing_rejected",
                slot_elapsed_ms=slot_elapsed_ms,
            )

        return self._build_plan(
            question,
            intent,
            slots,
            confidence,
            started,
            candidate_intents=candidate_intents,
            matched_query=selected_candidate.matched_query,
            vector_distance=round(selected_candidate.distance, 4),
            slot_source=slot_source,
            slot_elapsed_ms=slot_elapsed_ms,
        )

    def _build_plan(
        self,
        question: str,
        intent: BusinessIntent,
        slots: dict[str, Any],
        confidence: float,
        started: float,
        *,
        candidate_intents: list[dict[str, Any]],
        matched_query: str | None,
        vector_distance: float | None,
        slot_source: str,
        slot_elapsed_ms: int,
    ) -> SemanticPlan:
        # TODO: 这里需要根据 intent 的状态来判断是否需要补充缺失的 slot
        missing_slots = [slot for slot in intent.required_slots if _empty(slots.get(slot))]
        status = intent.status
        reason = intent.reason
        if status == "metadata" and intent.intent_id == "field_explanation":
            if _empty(slots.get("field_name")) and (
                _empty(slots.get("table_name")) or _empty(slots.get("column_name"))
            ):
                status = "needs_clarification"
                reason = "请说明要查询哪个字段，例如：table_name.column_name。"
        elif status == "metadata":
            pass
        elif status == "executable" and missing_slots:
            status = "needs_clarification"
            reason = f"缺少必要条件：{', '.join(missing_slots)}"
        elif status == "executable" and not intent.template_id:
            status = "needs_mapping"
            reason = "该意图缺少可执行 SQL 模板。"

        return SemanticPlan(
            status=status,
            intent=intent.intent_id,
            display_name=intent.display_name,
            output_type=intent.output_type,
            confidence=confidence,
            slots=slots,
            missing_slots=missing_slots,
            candidate_tables=list(intent.physical_tables),
            template_id=intent.template_id if status == "executable" else None,
            ontology_refs=list(intent.ontology_refs),
            reason=reason,
            needs=list(intent.needs),
            elapsed_ms=self._elapsed_ms(started),
            candidate_intents=candidate_intents,
            matched_query=matched_query,
            vector_distance=vector_distance,
            slot_source=slot_source,
            slot_elapsed_ms=slot_elapsed_ms,
        )

    def _refresh_vector_index(self) -> bool:
        return self.vector_index.refresh([self._intent_vector_payload(intent) for intent in self.intents])

    def _candidate_intents(self, question: str) -> list[IntentVectorCandidate]:
        if not self.vector_index.enabled:
            return []
        if not self._refresh_vector_index():
            return []
        top_k = self.vector_index.config.top_k
        candidates = [
            candidate
            for candidate in self.vector_index.search(
                question,
                top_k=top_k,
            )
            if candidate.intent_id in self._intents_by_id
        ]
        lexical = self._lexical_candidate(question) # 获取match命中的意图
        if lexical:
            candidates = [
                lexical
                if candidate.intent_id == lexical.intent_id and lexical.distance < candidate.distance
                else candidate
                for candidate in candidates
            ]
            if all(candidate.intent_id != lexical.intent_id for candidate in candidates):
                candidates.append(lexical)
        # 严格按配置的 top_k 截断，保证 TEXT2SQL_INTENT_VECTOR_TOP_K 真正生效
        # （search 已对向量候选做 top_k 截断，此处再对“向量+词法合并”后的结果兜底截断）
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.distance,
                -self._intents_by_id[candidate.intent_id].priority,
            ),
        )[:top_k]

    def _project_intent(
        self, intent: BusinessIntent, distance: float, matched_query: str
    ) -> dict[str, Any]:
        return {
            "intent": intent.intent_id,
            "distance": round(distance, 4),
            "matchedQuery": matched_query,
            "id": intent.intent_id,
            "display_name": intent.display_name,
            "status": intent.status,
            "output_type": intent.output_type,
            "required_slots": list(intent.required_slots),
            "optional_slots": list(intent.optional_slots),
            "physical_tables": list(intent.physical_tables),
            "template": intent.template_id,
            "ontology_refs": list(intent.ontology_refs),
            "reason": intent.reason,
            "needs": list(intent.needs),
            "examples": list(intent.examples),
            "slot_defaults": intent.slot_defaults,
            "remark": intent.remark,
        }

    def _candidate_projection(self, candidate: IntentVectorCandidate) -> dict[str, Any]:
        intent = self._intents_by_id[candidate.intent_id]
        payload = candidate.to_dict()
        payload.update(self._project_intent(intent, candidate.distance, candidate.matched_query))
        return payload

    def _candidate_for_intent(
        self,
        candidates: list[IntentVectorCandidate],
        intent_id: str,
    ) -> IntentVectorCandidate | None:
        for candidate in candidates:
            if candidate.intent_id == intent_id:
                return candidate
        return None

    def _extract_slots_with_llm(
        self,
        question: str,
        candidate_intents: list[dict[str, Any]],
        history: list[dict[str, object]] | None,
    ) -> SlotExtractionResult | None:
        if self.slot_extractor is None:
            return None
        return self.slot_extractor.extract(question, candidate_intents, history)

    def _extract_slots_via_llm(
        self,
        question: str,
        intents: list[BusinessIntent],
        history: list[dict[str, object]] | None,
        confidence: float,
    ) -> tuple[dict[str, Any] | None, str, int]:
        """用 LLM 为给定意图抽取槽位。

        返回 ``(slots, slot_source, elapsed_ms)``；LLM 不可用 / 抽取失败 / 判定 fallback 时返回
        ``(None, source, elapsed_ms)``，调用方应回退到启发式槽位。
        """
        if not intents or self.slot_extractor is None:
            return None, "llm_unavailable", 0
        candidate_intents = [
            self._project_intent(intent, 1.0 - confidence, "keyword_match") for intent in intents
        ]
        extraction = self._extract_slots_with_llm(question, candidate_intents, history)
        if extraction is None:
            return None, "llm_extraction_failed", 0
        if extraction.decision == "fallback":
            return None, "llm_fallback", extraction.elapsed_ms
        # lexical 已确认意图（已通过闸），仅借用 LLM 抽取出的槽位
        intent = self._intents_by_id.get(extraction.intent_id) or intents[0]
        slots = self._complete_slots(
            question,
            intent,
            extraction.slots,
            use_heuristic=False,
        )
        return slots, f"llm_slot_{extraction.source}", extraction.elapsed_ms

    def _complete_slots(
        self,
        question: str,
        intent: BusinessIntent,
        extracted_slots: dict[str, Any],
        *,
        use_heuristic: bool,
    ) -> dict[str, Any]:
        slots: dict[str, Any] = dict(intent.slot_defaults)
        if use_heuristic:
            for key, value in self._extract_slots(question, intent).items():
                if not _empty(value):
                    slots[key] = value # 设置系统内置的参数
        for key, value in extracted_slots.items():
            if not _empty(value):
                slots[key] = value
        self._derive_slots(intent, slots) # 设置系统内置参数默认值？？
        if intent.template_id == "dynamic_entity_query":
            self.entity_query_compiler.complete_slots(question, slots)
        if not use_heuristic and any(_empty(slots.get(slot)) for slot in intent.required_slots):
            for key, value in self._extract_slots(question, intent).items():
                if _empty(slots.get(key)) and not _empty(value):
                    slots[key] = value
            self._derive_slots(intent, slots)
            if intent.template_id == "dynamic_entity_query":
                self.entity_query_compiler.complete_slots(question, slots)
        return slots

    def _derive_slots(self, intent: BusinessIntent, slots: dict[str, Any]) -> None:
        derive_slots(
            required_slots=intent.required_slots,
            optional_slots=intent.optional_slots,
            slots=slots,
        )

    def _intent_vector_payload(self, intent: BusinessIntent) -> dict[str, Any]:
        semantic: dict[str, Any] = {
            "queries": list(intent.semantic_queries),
            "negative_queries": list(intent.semantic_negative_queries),
            "boundary_queries": list(intent.semantic_boundary_queries),
            "boundary_negative_queries": list(intent.semantic_boundary_negative_queries),
        }
        return {
            "id": intent.intent_id,
            "display_name": intent.display_name,
            "status": intent.status,
            "priority": intent.priority,
            "examples": list(intent.examples),
            "semantic": semantic,
        }

    def compile(self, plan: SemanticPlan) -> GeneratedSql:
        if plan.status != "executable" or not plan.template_id:
            raise RejectedQuery(plan.reason or "该语义意图不可执行", plan.status)
        if plan.template_id == "dynamic_entity_query":
            return self.entity_query_compiler.compile(plan.slots)
        template = self.templates.get(plan.template_id)
        if template is None:
            raise RejectedQuery(f"缺少 SQL 模板: {plan.template_id}", "template_not_found")
        sql = self._render_sql(template.sql, plan.slots)
        params = {
            param_name: plan.slots[slot_name]
            for param_name, slot_name in template.params.items()
            if not _empty(plan.slots.get(slot_name))
        }
        params = encrypt_sensitive_query_params(
            params,
            intent_id=plan.intent or "",
            settings=self.field_encryption,
        )
        log = {
            "kind": "semantic_template",
            "status": "ok",
            "intent": plan.intent,
            "templateId": template.template_id,
            "plan": template.plan,
            "sql": sql,
            "paramKeys": sorted(params),
        }
        return GeneratedSql(
            sql=sql,
            plan=template.plan,
            hit_path="semantic_template",
            params=params,
            interaction_logs=[log],
        )

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "id": intent.intent_id,
                "display_name": intent.display_name,
                "status": intent.status,
                "output_type": intent.output_type,
                "template": intent.template_id,
                "physical_tables": list(intent.physical_tables),
                "ontology_refs": list(intent.ontology_refs),
                "required_slots": list(intent.required_slots),
                "optional_slots": list(intent.optional_slots),
                "reason": intent.reason,
                "needs": list(intent.needs),
                "examples": list(intent.examples),
                "semantic_queries": list(intent.semantic_queries),
            }
            for intent in self.intents
        ]

    def _best_intent(self, question: str) -> tuple[BusinessIntent, float] | None:
        scored: list[tuple[int, int, BusinessIntent]] = []
        single_hit: list[tuple[int, int, BusinessIntent]] = []
        lowered = question.lower()
        for order, intent in enumerate(self.intents):
            if any(keyword.lower() in lowered for keyword in intent.match_none):
                continue # 命中任意match_none
            if intent.match_all and not all(keyword.lower() in lowered for keyword in intent.match_all):
                continue # 没有全部命中match_all
            hits = sum(1 for keyword in intent.match_any if keyword.lower() in lowered)
            example_hits = sum(1 for example in intent.examples if example and example in question)
            if intent.match_any and hits == 0 and example_hits == 0:
                continue # 存在match_any 且未命中任何一个
            strict = True
            if intent.match_any and not intent.match_all: # 有any且没有all
                if example_hits == 0 and hits < self.routing.min_lexical_keyword_hits: # hit命中少于2
                    long_hits = sum(
                        1
                        for keyword in intent.match_any
                        if len(keyword) >= 5 and keyword.lower() in lowered
                    )
                    if long_hits == 0:
                        strict = False # 未命中>=5字符的长关键词，单短关键词不足以走严格闸
            score = intent.priority + hits + (example_hits * 2)
            if strict:
                scored.append((score, -order, intent))
            else:
                single_hit.append((score, -order, intent))
        # 严格闸无命中时，若只有一个意图命中了短关键词（无竞争），仍视为明确命中，
        # 以支持 "部门名+人数" 这类无法用子串枚举的问法；多意图同时单命中则保持拒绝（防歧义）。
        candidates = scored or (single_hit if len(single_hit) == 1 else [])
        if not candidates:
            return None
        score, _, intent = max(candidates, key=lambda item: (item[0], item[1]))
        confidence = min(0.99, max(0.5, score / 100))
        return intent, round(confidence, 2)

    def _extract_slots(self, question: str, intent: BusinessIntent) -> dict[str, Any]:
        return extract_slots(
            question,
            intent_id=intent.intent_id,
            required_slots=intent.required_slots,
            optional_slots=intent.optional_slots,
            slot_defaults=intent.slot_defaults,
        )

    def _normalized_question(self, question: str) -> str:
        return re.sub(r"\s+", "", question.strip())

    # 是否命中实例
    def _question_matches_intent_example(self, question: str, intent: BusinessIntent) -> bool:
        normalized_question = self._normalized_question(question)
        for example in intent.examples:
            if not example:
                continue
            normalized_example = self._normalized_question(example)
            if normalized_question == normalized_example or example in question:
                return True
        return False

    def _candidate_gap(self, candidates: list[IntentVectorCandidate]) -> float:
        if len(candidates) < 2:
            return 1.0
        return candidates[1].distance - candidates[0].distance

    def _passes_lexical_only_gate(
        self,
        question: str,
        intent: BusinessIntent,
        confidence: float,
    ) -> bool:
        if self._question_matches_intent_example(question, intent): # 命中实例
            return True
        if confidence >= self.routing.min_executable_confidence: # 置信度大于0.58
            return True
        return False

    def _is_ambiguous_candidate_set(self, candidates: list[IntentVectorCandidate]) -> bool:
        if len(candidates) < 2:
            return False
        first = candidates[0]
        second = candidates[1]
        if first.distance > self.routing.executable_max_distance:
            return False
        if second.distance > self.routing.executable_max_distance:
            return False
        return self._candidate_gap(candidates) < self.routing.min_ambiguity_gap

    def _passes_executable_routing_gate(
        self,
        question: str,
        intent: BusinessIntent,
        selected_candidate: IntentVectorCandidate,
        candidates: list[IntentVectorCandidate],
    ) -> bool:
        if intent.status != "executable":
            return True
        if self._question_matches_intent_example(question, intent):
            return True
        if selected_candidate.distance <= 0.01:
            return True
        lexical = self._lexical_candidate(question)
        if (
            lexical is not None
            and lexical.intent_id == intent.intent_id
            and lexical.distance <= self.routing.strong_lexical_distance
        ):
            return True
        if selected_candidate.matched_query == "keyword_match" and lexical is not None:
            if (
                lexical.intent_id == intent.intent_id
                and lexical.distance <= self.routing.strong_lexical_distance + 0.02
            ):
                return True
        distance = selected_candidate.distance
        if distance > self.routing.executable_max_distance:
            return False
        if _confidence_from_distance(distance) < self.routing.min_executable_confidence:
            return False
        if self._is_ambiguous_candidate_set(candidates):
            return False
        return True

    # 快速启发式意图
    def _try_fast_heuristic_plan(
        self,
        question: str,
        candidates: list[IntentVectorCandidate],
        candidate_intents: list[dict[str, Any]],
        started: float,
    ) -> SemanticPlan | None:
        if not candidates:
            return None
        selected_candidate = candidates[0]
        intent = self._intents_by_id.get(selected_candidate.intent_id)
        if intent is None:
            return None
        if not self._should_skip_llm_for_intent(question, intent, selected_candidate, candidates):
            return None # false不应该跳过LLM, true 跳过，继续往下走
        slots = self._complete_slots(question, intent, {}, use_heuristic=True)
        # 已配置 LLM 且存在启发式填不上的可选槽位（如部门名）时，不抢跑，交给 LLM 抽取
        if self._llm_configured and self._llm_slot_policy != "never":
            unfilled_optional = [slot for slot in intent.optional_slots if _empty(slots.get(slot))]
            if unfilled_optional:
                return None
        if not self._heuristic_plan_ready(intent, slots):
            return None
        confidence = _confidence_from_distance(selected_candidate.distance)
        intent, selected_candidate, slots, confidence, slot_source = self._apply_strong_lexical_override(
            question,
            intent,
            selected_candidate,
            slots,
            confidence,
            "heuristic_fast_path",
            {},
        )
        return self._build_plan(
            question,
            intent,
            slots,
            confidence,
            started,
            candidate_intents=candidate_intents,
            matched_query=selected_candidate.matched_query,
            vector_distance=round(selected_candidate.distance, 4),
            slot_source=slot_source,
            slot_elapsed_ms=0,
        )

    def _should_skip_llm_for_intent(
        self,
        question: str,
        intent: BusinessIntent,
        selected_candidate: IntentVectorCandidate,
        candidates: list[IntentVectorCandidate],
    ) -> bool:
        normalized_question = re.sub(r"\s+", "", question.strip())
        for example in intent.examples:
            if not example:
                continue
            normalized_example = re.sub(r"\s+", "", example.strip())
            if normalized_question == normalized_example or example in question:
                return True
        if selected_candidate.distance <= self.routing.fast_path_max_distance:
            if len(candidates) == 1:
                return True
            if self._candidate_gap(candidates) >= self.routing.min_candidate_gap:
                return True
        lexical = self._lexical_candidate(question)
        if (
            lexical is not None
            and lexical.intent_id == intent.intent_id
            and lexical.distance <= self.routing.strong_lexical_distance
        ):
            return True
        return False

    def _heuristic_plan_ready(self, intent: BusinessIntent, slots: dict[str, Any]) -> bool:
        if intent.status == "metadata":
            return intent.intent_id == "field_explanation" and (
                not _empty(slots.get("field_name"))
                or (not _empty(slots.get("table_name")) and not _empty(slots.get("column_name")))
            )
        if intent.status != "executable":
            return False
        missing_slots = [slot for slot in intent.required_slots if _empty(slots.get(slot))]
        if missing_slots:
            return False
        if intent.template_id == "dynamic_entity_query":
            entity_query = slots.get("entity_query")
            if not isinstance(entity_query, dict):
                return False
            return not _empty(entity_query.get("entity"))
        return bool(intent.template_id)

    def _lexical_candidate(self, question: str) -> IntentVectorCandidate | None:
        matched = self._best_intent(question)
        if matched is None:
            return None
        intent, confidence = matched
        return IntentVectorCandidate(
            intent_id=intent.intent_id,
            distance=round(max(0.0, 1.0 - confidence), 4),
            matched_query="keyword_match",
        )

    def _apply_strong_lexical_override(
        self,
        question: str,
        intent: BusinessIntent,
        selected_candidate: IntentVectorCandidate,
        slots: dict[str, Any],
        confidence: float,
        slot_source: str,
        extracted_slots: dict[str, Any],
    ) -> tuple[BusinessIntent, IntentVectorCandidate, dict[str, Any], float, str]:
        lexical = self._lexical_candidate(question)
        if lexical is None or lexical.distance > self.routing.strong_lexical_distance:
            return intent, selected_candidate, slots, confidence, slot_source

        lexical_intent = self._intents_by_id.get(lexical.intent_id)
        if lexical_intent is None or lexical_intent.status not in {"executable", "metadata"}:
            return intent, selected_candidate, slots, confidence, slot_source

        if intent.intent_id == lexical_intent.intent_id and intent.status in {"executable", "metadata"}:
            return intent, selected_candidate, slots, confidence, slot_source

        merged_slots = self._complete_slots(
            question,
            lexical_intent,
            extracted_slots,
            use_heuristic=True,
        )
        return (
            lexical_intent,
            lexical,
            merged_slots,
            max(confidence, round(1.0 - lexical.distance, 2)),
            f"{slot_source}_lexical_override",
        )

    def _render_sql(self, sql: str, slots: dict[str, Any]) -> str:
        values = computed_values(slots)

        def optional_block(match: re.Match[str]) -> str:
            slot_name = match.group(1)
            body = match.group(2)
            return body if _lenGT1(slots.get(slot_name)) else ""

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise RejectedQuery(f"SQL 模板缺少安全计算槽位: {name}", "template_slot_missing")
            return str(values[name])

        sql = OPTIONAL_BLOCK_RE.sub(optional_block, sql)
        return PLACEHOLDER_RE.sub(replace, sql)

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)


def _confidence_from_distance(distance: float) -> float:
    return round(max(0.0, min(0.99, 1.0 - distance)), 2)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    """将 YAML 中的 remark 兼容为字符串元组。

    支持三种写法：``None`` → ``()``、单个字符串 → ``(str,)``、列表 → 逐项转字符串。
    这样可同时兼容 ``remark: "..."`` 与 ``remark:\n  - "..."`` 两种配置形式。
    """
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _lenGT1(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and len(value.strip()) > 1:
        return True
    return False
