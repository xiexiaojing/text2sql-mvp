from __future__ import annotations

import datetime as dt
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .address_parser import build_room_name_path_likes, parse_room_address
from .entity_query import EntityQueryCompiler
from .intent_vector import IntentVectorCandidate, IntentVectorIndex, build_intent_vector_index
from .models import GeneratedSql, RejectedQuery
from .rejection_reasons import UNCONFIGURED_SEMANTIC_REASON
from .semantic_slot_extractor import LlmSlotExtractor, SlotExtractionResult
from .semantics import (
    epoch_ms_for_age_at_least,
    month_end_epoch_ms,
    month_start_epoch_ms,
    week_end_epoch_ms,
    week_start_epoch_ms,
)
from .config import IntentVectorSettings, LlmSettings, load_yaml

CHINESE_NAME_RE = re.compile(r"[\u4e00-\u9fa5]{2,4}")
PHONE_RE = re.compile(r"(?<!\d)(1\d{10})(?!\d)")
CARD_RE = re.compile(r"(?<![A-Za-z0-9])([0-9Xx]{15,18})(?![A-Za-z0-9])")
PLACEHOLDER_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
OPTIONAL_BLOCK_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(.*?)\]\]", re.DOTALL)
_STRONG_LEXICAL_DISTANCE = 0.05
_FAST_LLM_SKIP_VECTOR_DISTANCE = 0.35


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
    ) -> None:
        self.entities = entities
        self.intents = intents
        self.templates = templates
        self.entity_query_compiler = entity_query_compiler or EntityQueryCompiler({})
        self._intents_by_id = {intent.intent_id: intent for intent in intents}
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
            )
        raw = load_yaml(path)
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
        )

    def plan(self, question: str, history: list[dict[str, object]] | None = None) -> SemanticPlan:
        started = time.monotonic()
        candidates = self._candidate_intents(question)
        if not candidates and not self.vector_index.enabled:
            matched = self._best_intent(question)
            if matched is not None:
                intent, confidence = matched
                slots = self._complete_slots(
                    question,
                    intent,
                    {},
                    use_heuristic=True,
                )
                return self._build_plan(
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
        if extraction and extraction.intent_id and extraction.intent_id in self._intents_by_id:
            intent = self._intents_by_id[extraction.intent_id]
            selected_candidate = self._candidate_for_intent(candidates, intent.intent_id) or candidates[0]
            slots = self._complete_slots(
                question,
                intent,
                extraction.slots,
                use_heuristic=False,
            )
            confidence = extraction.confidence or _confidence_from_distance(selected_candidate.distance)
            slot_source = extraction.source
            slot_elapsed_ms = extraction.elapsed_ms
        else:
            selected_candidate = candidates[0]
            intent = self._intents_by_id[selected_candidate.intent_id]
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

        return self._build_plan(
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
        missing_slots = [slot for slot in intent.required_slots if _empty(slots.get(slot))]
        status = intent.status
        reason = intent.reason
        if status == "metadata" and intent.intent_id == "field_explanation":
            if _empty(slots.get("field_name")) and (
                _empty(slots.get("table_name")) or _empty(slots.get("column_name"))
            ):
                status = "needs_clarification"
                reason = "请说明要查询哪个字段，例如：resident.residence_status 或「居住状况」。"
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
        candidates = [
            candidate
            for candidate in self.vector_index.search(
                question,
                top_k=max(6, self.vector_index.config.top_k),
            )
            if candidate.intent_id in self._intents_by_id
        ]
        lexical = self._lexical_candidate(question)
        if lexical:
            candidates = [
                lexical
                if candidate.intent_id == lexical.intent_id and lexical.distance < candidate.distance
                else candidate
                for candidate in candidates
            ]
            if all(candidate.intent_id != lexical.intent_id for candidate in candidates):
                candidates.append(lexical)
        return sorted(
            candidates,
            key=lambda candidate: (
                candidate.distance,
                -self._intents_by_id[candidate.intent_id].priority,
            ),
        )

    def _candidate_projection(self, candidate: IntentVectorCandidate) -> dict[str, Any]:
        intent = self._intents_by_id[candidate.intent_id]
        payload = candidate.to_dict()
        payload.update(
            {
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
            }
        )
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
                    slots[key] = value
        for key, value in extracted_slots.items():
            if not _empty(value):
                slots[key] = value
        self._derive_slots(intent, slots)
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
        requested_slots = set(intent.required_slots) | set(intent.optional_slots) | set(slots)
        if "age" in requested_slots and _empty(slots.get("age")):
            slots["age"] = 60
        if "age_cutoff_ms" in requested_slots or "age" in requested_slots:
            age = int(slots.get("age") or 60)
            slots["age_cutoff_ms"] = epoch_ms_for_age_at_least(age)
        if "month_start_ms" in requested_slots:
            slots["month_start_ms"] = month_start_epoch_ms()
        if "year_start_ms" in requested_slots:
            slots["year_start_ms"] = _year_start_epoch_ms()
        if not _empty(slots.get("sexual")):
            normalized_sexual = _normalize_sexual(str(slots["sexual"]))
            if normalized_sexual:
                slots["sexual"] = normalized_sexual
        if not _empty(slots.get("marital_status")):
            normalized_marital_status = _normalize_marital_status(str(slots["marital_status"]))
            if normalized_marital_status:
                slots["marital_status"] = normalized_marital_status
        if not _empty(slots.get("role_like")):
            slots["role_like"] = _normalize_like_value(str(slots["role_like"]))
        elif "role_like" in requested_slots and not _empty(slots.get("role")):
            slots["role_like"] = _like_value(str(slots["role"]))
        if not _empty(slots.get("tag_like")):
            slots["tag_like"] = _normalize_like_value(str(slots["tag_like"]))
        elif "tag_like" in requested_slots and not _empty(slots.get("tag_name")):
            tag_name = str(slots["tag_name"])
            slots["tag_like"] = "%独居%" if tag_name.startswith("独居") else _like_value(tag_name)
        if not _empty(slots.get("party_branch_path_like")):
            slots["party_branch_path_like"] = _normalize_party_branch_path_like(
                str(slots["party_branch_path_like"])
            )
        elif not _empty(slots.get("party_branch_name")):
            slots["party_branch_path_like"] = _party_branch_member_path_like(
                str(slots["party_branch_name"])
            )
        like_slots = {
            "merchant_name": "merchant_name_like",
            "area_name": "area_like",
            "category": "category_like",
            "field_name": "field_like",
            "skill": "skill_like",
            "grid_name": "grid_name_like",
            "person_name": "person_name_like",
        }
        for slot_name, like_slot in like_slots.items():
            if not _empty(slots.get(like_slot)):
                slots[like_slot] = _normalize_like_value(str(slots[like_slot]))
            elif not _empty(slots.get(slot_name)):
                slots[like_slot] = _like_value(str(slots[slot_name]))
        if not _empty(slots.get("field_like")) and "tag_like" in requested_slots:
            slots.setdefault("tag_like", str(slots["field_like"]))
        if (
            "address_like" in requested_slots
            and not _empty(slots.get("address_like"))
        ):
            _derive_address_like_slots(slots, str(slots["address_like"]))
        elif (
            "address_like" in requested_slots
            and not _empty(slots.get("address"))
        ):
            _derive_address_like_slots(slots, str(slots["address"]))

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
        lowered = question.lower()
        for order, intent in enumerate(self.intents):
            if any(keyword.lower() in lowered for keyword in intent.match_none):
                continue
            if intent.match_all and not all(keyword.lower() in lowered for keyword in intent.match_all):
                continue
            hits = sum(1 for keyword in intent.match_any if keyword.lower() in lowered)
            example_hits = sum(1 for example in intent.examples if example and example in question)
            if intent.match_any and hits == 0 and example_hits == 0:
                continue
            score = intent.priority + hits + (example_hits * 2)
            scored.append((score, -order, intent))
        if not scored:
            return None
        score, _, intent = max(scored, key=lambda item: (item[0], item[1]))
        confidence = min(0.99, max(0.5, score / 100))
        return intent, round(confidence, 2)

    def _extract_slots(self, question: str, intent: BusinessIntent) -> dict[str, Any]:
        slots: dict[str, Any] = dict(intent.slot_defaults)
        requested_slots = set(intent.required_slots) | set(intent.optional_slots) | set(slots)
        if "age" in requested_slots:
            slots.setdefault("age", _extract_age(question) or 60)
        if "age_cutoff_ms" in requested_slots or "age" in requested_slots:
            age = int(slots.get("age") or 60)
            slots["age_cutoff_ms"] = epoch_ms_for_age_at_least(age)
        if "month_start_ms" in requested_slots:
            slots["month_start_ms"] = month_start_epoch_ms()
        if "month_end_ms" in requested_slots:
            slots["month_end_ms"] = month_end_epoch_ms()
        if "week_start_ms" in requested_slots:
            slots["week_start_ms"] = week_start_epoch_ms()
        if "week_end_ms" in requested_slots:
            slots["week_end_ms"] = week_end_epoch_ms()
        if "current_year" in requested_slots:
            slots["current_year"] = dt.date.today().year
        if "current_month" in requested_slots:
            slots["current_month"] = dt.date.today().month
        if "apply_month_scope" in requested_slots and _question_has_month_scope(question):
            slots["apply_month_scope"] = True
        if "apply_week_scope" in requested_slots and _question_has_week_scope(question):
            slots["apply_week_scope"] = True
        if "year_start_ms" in requested_slots:
            slots["year_start_ms"] = _year_start_epoch_ms()
        if "sexual" in requested_slots:
            sexual = _extract_sexual(question, slots.get("sexual"))
            if sexual:
                slots["sexual"] = sexual
        if "marital_status" in requested_slots:
            marital_status = _extract_marital_status(question, slots.get("marital_status"))
            if marital_status:
                slots["marital_status"] = marital_status
        if "person_name" in requested_slots:
            name = _extract_person_name(question, intent.intent_id)
            if name:
                slots["person_name"] = name
        if "party_branch_name" in requested_slots:
            branch = _extract_party_branch(question)
            if branch:
                slots["party_branch_name"] = branch
        if "grid_name" in requested_slots:
            grid_name = _extract_grid_name(question)
            if grid_name:
                slots["grid_name"] = grid_name
        if "address" in requested_slots or "address_like" in requested_slots:
            address = _extract_address(question)
            if address:
                address = _normalize_compound_building_address(address)
                slots["address"] = address
                slots["address_like"] = _like_value(address)
        if "role" in requested_slots or "role_like" in requested_slots:
            role = _extract_role(question, intent.intent_id, slots.get("role"))
            if role:
                slots["role"] = role
                slots["role_like"] = _like_value(role)
        if "tag_name" in requested_slots:
            tag_name = _extract_tag_name(question, slots.get("tag_name"))
            if tag_name:
                slots["tag_name"] = tag_name
        if "tag_like" in requested_slots and not _empty(slots.get("tag_name")):
            tag_name = str(slots["tag_name"])
            slots["tag_like"] = "%独居%" if tag_name.startswith("独居") else _like_value(tag_name)
        if "skill" in requested_slots or "skill_like" in requested_slots:
            skill = _extract_skill(question, slots.get("skill"))
            if skill:
                slots["skill"] = skill
                slots["skill_like"] = _like_value(skill)
        if "merchant_name" in requested_slots or "merchant_name_like" in requested_slots:
            merchant_name = _extract_merchant_name(question)
            if merchant_name:
                slots["merchant_name"] = merchant_name
                slots["merchant_name_like"] = _like_value(merchant_name)
        if "area_name" in requested_slots or "area_like" in requested_slots:
            area_name = _extract_area_name(question)
            if area_name:
                slots["area_name"] = area_name
                slots["area_like"] = _like_value(area_name)
        if "category" in requested_slots or "category_like" in requested_slots:
            category = _extract_category(question, slots.get("category"))
            if category:
                slots["category"] = category
                slots["category_like"] = _like_value(category)
        if "field_name" in requested_slots or "field_like" in requested_slots:
            field_name = _extract_field_name(question, slots.get("field_name"))
            if field_name:
                slots["field_name"] = field_name
                slots["field_like"] = _like_value(field_name)
        if "result_limit" in requested_slots:
            slots["result_limit"] = _extract_result_limit(question, int(slots.get("result_limit") or 10))
        if intent.intent_id == "field_explanation":
            match = re.search(
                r"\b([a-z][a-z0-9_]*)\s*\.\s*([a-z][a-z0-9_]*)\b",
                question,
                re.IGNORECASE,
            )
            if match:
                slots.setdefault("table_name", match.group(1).lower())
                slots.setdefault("column_name", match.group(2).lower())
                slots.setdefault("field_name", f"{match.group(1).lower()}.{match.group(2).lower()}")
        if "phone" in requested_slots:
            match = PHONE_RE.search(question)
            if match:
                slots["phone"] = match.group(1)
        if "card_no" in requested_slots:
            match = CARD_RE.search(question)
            if match:
                slots["card_no"] = match.group(1)
        return slots

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
            return None
        slots = self._complete_slots(question, intent, {}, use_heuristic=True)
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
        if selected_candidate.distance <= _FAST_LLM_SKIP_VECTOR_DISTANCE:
            if len(candidates) == 1:
                return True
            second_distance = candidates[1].distance
            if second_distance - selected_candidate.distance >= 0.08:
                return True
        lexical = self._lexical_candidate(question)
        if (
            lexical is not None
            and lexical.intent_id == intent.intent_id
            and lexical.distance <= _STRONG_LEXICAL_DISTANCE
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
        if lexical is None or lexical.distance > _STRONG_LEXICAL_DISTANCE:
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
        values = _computed_values(slots)

        def optional_block(match: re.Match[str]) -> str:
            slot_name = match.group(1)
            body = match.group(2)
            return body if not _empty(slots.get(slot_name)) else ""

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in values:
                raise RejectedQuery(f"SQL 模板缺少安全计算槽位: {name}", "template_slot_missing")
            return str(values[name])

        sql = OPTIONAL_BLOCK_RE.sub(optional_block, sql)
        return PLACEHOLDER_RE.sub(replace, sql)

    def _elapsed_ms(self, started: float) -> int:
        return int((time.monotonic() - started) * 1000)


def _question_has_month_scope(question: str) -> bool:
    return any(marker in question for marker in ("本月", "这个月", "当月"))


def _question_has_week_scope(question: str) -> bool:
    return any(marker in question for marker in ("本周", "这周", "当周"))


def _computed_values(slots: dict[str, Any]) -> dict[str, int]:
    age = int(slots.get("age") or 60)
    today = dt.date.today()
    return {
        "age_cutoff_ms": int(slots.get("age_cutoff_ms") or epoch_ms_for_age_at_least(age)),
        "age_cutoff_18_ms": int(slots.get("age_cutoff_18_ms") or epoch_ms_for_age_at_least(18)),
        "age_cutoff_35_ms": int(slots.get("age_cutoff_35_ms") or epoch_ms_for_age_at_least(35)),
        "age_cutoff_60_ms": int(slots.get("age_cutoff_60_ms") or epoch_ms_for_age_at_least(60)),
        "month_start_ms": int(slots.get("month_start_ms") or month_start_epoch_ms()),
        "month_end_ms": int(slots.get("month_end_ms") or month_end_epoch_ms(today)),
        "week_start_ms": int(slots.get("week_start_ms") or week_start_epoch_ms(today)),
        "week_end_ms": int(slots.get("week_end_ms") or week_end_epoch_ms(today)),
        "year_start_ms": int(slots.get("year_start_ms") or _year_start_epoch_ms(today)),
        "last_year_start_ms": int(slots.get("last_year_start_ms") or _year_start_epoch_ms(_add_years(today, -1))),
        "last_year_end_ms": int(slots.get("last_year_end_ms") or _year_start_epoch_ms(today)),
        "half_year_start_ms": int(slots.get("half_year_start_ms") or _date_to_epoch_ms(today - dt.timedelta(days=183))),
        "senior_next_year_birth_start_ms": int(
            slots.get("senior_next_year_birth_start_ms")
            or _date_to_epoch_ms(dt.date(today.year + 1 - 80, 1, 1))
        ),
        "senior_next_year_birth_end_ms": int(
            slots.get("senior_next_year_birth_end_ms")
            or _date_to_epoch_ms(dt.date(today.year + 2 - 80, 1, 1))
        ),
        "result_limit": int(slots.get("result_limit") or 10),
    }


def _year_start_epoch_ms(today: dt.date | None = None) -> int:
    current = today or dt.date.today()
    start = dt.date(current.year, 1, 1)
    return _date_to_epoch_ms(start)


def _date_to_epoch_ms(value: dt.date) -> int:
    return int(dt.datetime.combine(value, dt.time.min).timestamp() * 1000)


def _add_years(value: dt.date, years: int) -> dt.date:
    return dt.date(value.year + years, value.month, value.day)


def _extract_age(question: str) -> int | None:
    match = re.search(r"(\d{2,3})\s*岁", question)
    if not match:
        return None
    return int(match.group(1))


def _extract_sexual(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return _normalize_sexual(default)
    if any(keyword in question for keyword in ["女性", "女党员", "女居民", "女"]):
        return "女"
    if any(keyword in question for keyword in ["男性", "男党员", "男居民", "男"]):
        return "男"
    return None


def _extract_marital_status(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return _normalize_marital_status(default)
    for value in ["未婚", "已婚", "离异", "离婚", "丧偶"]:
        if value in question:
            return "离异" if value == "离婚" else value
    return None


def _normalize_sexual(value: str) -> str | None:
    lowered = value.strip().lower()
    if lowered in {"female", "woman", "women", "f"}:
        return "女"
    if lowered in {"male", "man", "men", "m"}:
        return "男"
    if "女" in value:
        return "女"
    if "男" in value:
        return "男"
    return value or None


def _normalize_marital_status(value: str) -> str | None:
    lowered = value.strip().lower()
    if lowered in {"unmarried", "single"}:
        return "未婚"
    if lowered == "married":
        return "已婚"
    if lowered == "divorced":
        return "离异"
    if lowered == "widowed":
        return "丧偶"
    if "未婚" in value:
        return "未婚"
    if "已婚" in value:
        return "已婚"
    if "离异" in value or "离婚" in value:
        return "离异"
    if "丧偶" in value:
        return "丧偶"
    return value or None


def _extract_person_name(question: str, intent_id: str) -> str | None:
    patterns = [
        r"叫([\u4e00-\u9fa5]{2,4}?)(?=的|居民|吗|[，。？！?]|$)",
        r"(?:党支部|支部)的([\u4e00-\u9fa5]{2,4}?)(?=党龄|入党|的|[，。？！?]|$)",
        r"([\u4e00-\u9fa5]{2,4}?)(?=党龄|入党)",
        r"([\u4e00-\u9fa5]{2,4}?)(?=负责|职责|工作)",
        r"离职的([\u4e00-\u9fa5]{2,4}?)(?=什么时候|以前|[，。？！?]|$)",
        r"搬走的([\u4e00-\u9fa5]{2,4}?)(?=原来|以前|住|[，。？！?]|$)",
        r"([\u4e00-\u9fa5]{2,4}?)(?:大爷|阿姨|老人)?家(?=上次|几口|有没有|[，。？！?]|$)",
        r"([\u4e00-\u9fa5]{2,4}?)(?=一般都是几点|一般几点|去年的走访|走访数)",
        r"(?:查看|查询|查一下)?([\u4e00-\u9fa5]{2,4}?)(?=居民全息档案|居民档案|全息档案)",
        r"([\u4e00-\u9fa5]{2,4}?)(?=历史走访记录|历史走访|走访记录)",
        r"(?:居民|人员|党员)([\u4e00-\u9fa5]{2,4}?)(?=的|信息|档案|[，。？！?]|$)",
        r"([\u4e00-\u9fa5]{2,4}?)(?:的)?(?=个人资料|住户资料|居民资料|信息|资料|档案|详情)",
    ]
    if intent_id == "employee_position_holder":
        return None
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            return match.group(1)
    names = [
        item
        for item in CHINESE_NAME_RE.findall(question)
        if item not in {"本社区", "社区", "网格", "党员", "党支部", "居民"}
    ]
    return names[0] if names else None


def _extract_party_branch(question: str) -> str | None:
    match = re.search(r"((?:第[一二三四五六七八九十0-9]+|[\u4e00-\u9fa5A-Za-z0-9]+)党支部)", question)
    return match.group(1) if match else None


def _extract_grid_name(question: str) -> str | None:
    preferred = re.search(r"(第[一二三四五六七八九十0-9]+网格)", question)
    if preferred:
        return preferred.group(1)
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{1,12}网格)", question)
    return match.group(1) if match else None


def _extract_address(question: str) -> str | None:
    patterns = [
        r"(.+?)(?:下|里|内)?(?:有多少|多少)(?:个)?(?:房间|房屋|房)",
        r"(.+?)(?:下|里|内)?(?:的)?(?:房间|房屋|房)(?:数量|数)",
        r"(.+?)(?:是不是|是否)出租房",
        r"(.+?)(?:归谁管|归哪个网格|属于哪个网格|谁负责)",
        r"(?:查询|查一下)?(.+?)(?:的)?(?:网格负责人|负责人)",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if not match:
            continue
        address = _clean_slot_text(match.group(1))
        if address:
            return address
    return None


def _extract_role(question: str, intent_id: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return default
    role_keywords = [
        "纪委书记",
        "党支部书记",
        "书记",
        "网格员",
        "社工",
        "主任",
        "副主任",
        "委员",
    ]
    for keyword in role_keywords:
        if keyword in question:
            if intent_id == "party_branch_secretary":
                return "书记"
            return keyword
    match = re.search(r"(.+?)(?:是谁|有多少人|多少人|一共多少人)", question)
    if match:
        role = _clean_slot_text(match.group(1))
        role = role.replace("社区", "")
        return role or None
    return None


def _extract_skill(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return default
    patterns = [
        r"(会[\u4e00-\u9fa5A-Za-z0-9]{1,12}?)(?:的)?志愿者",
        r"(会[\u4e00-\u9fa5A-Za-z0-9]{1,12}?)(?=的?居民|的?人员|有哪些|名单|$)",
        r"志愿者.*?(会[\u4e00-\u9fa5A-Za-z0-9]{1,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            skill = _clean_slot_text(match.group(1))
            if skill:
                return skill
    return None


def _extract_tag_name(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return default
    tags = ["退役军人", "失能老人", "独居老人", "独居老年人", "空巢老人", "残疾人", "高龄老人", "低保", "重点人群"]
    for tag in tags:
        if tag in question:
            return "独居老人" if tag == "独居老年人" else tag
    match = re.search(r"([\u4e00-\u9fa5]{2,8})(?:标签|群体|人群)", question)
    if not match:
        return None
    tag_name = match.group(1)
    return None if tag_name in {"标签", "人群", "群体", "特殊"} else tag_name


def _extract_merchant_name(question: str) -> str | None:
    patterns = [
        r"([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,20}?)(?=的联系人|联系人是谁|的负责人|负责人是谁)",
        r"(?:商户|店铺)([\u4e00-\u9fa5A-Za-z0-9（）()·]{2,20}?)(?=联系人|负责人)",
    ]
    for pattern in patterns:
        match = re.search(pattern, question)
        if match:
            value = _clean_slot_text(match.group(1))
            if value and value not in {"商户", "物业公司"}:
                return value
    return None


def _extract_area_name(question: str) -> str | None:
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]+(?:商业区|片区|小区|园区|街区))", question)
    return _clean_slot_text(match.group(1)) if match else None


def _extract_category(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return default
    categories = ["餐饮", "洗衣", "物业", "商超", "便利店", "药店"]
    for category in categories:
        if category in question:
            return category
    return None


def _extract_result_limit(question: str, default: int = 10) -> int:
    match = re.search(r"TOP\s*(\d+)", question, re.IGNORECASE)
    if match:
        return max(1, int(match.group(1)))
    match = re.search(r"前\s*(\d+)", question)
    if match:
        return max(1, int(match.group(1)))
    if "TOP3" in question.upper():
        return 3
    return default


def _extract_field_name(question: str, default: Any = None) -> str | None:
    if isinstance(default, str) and default:
        return default
    fields = ["低保", "残疾", "居住状况", "政治面貌", "手机号"]
    for field_value in fields:
        if field_value in question:
            return field_value
    match = re.search(r"[‘'“\"]([\u4e00-\u9fa5A-Za-z0-9_]+)[’'”\"]字段", question)
    return match.group(1) if match else None


def _clean_slot_text(value: str) -> str:
    cleaned = value.strip(" \t\r\n，。？！?的“”‘’\"'")
    for prefix in ["请问", "帮我查", "查询", "查一下", "本社区", "社区"]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip(" \t\r\n，。？！?的“”‘’\"'")


def _like_value(value: str) -> str:
    return f"%{value}%"


def _party_branch_member_path_like(value: str) -> str:
    branch = _clean_slot_text(value.strip().strip("%")).strip("/")
    return f"%/{branch}/%" if branch else value


def _normalize_party_branch_path_like(value: str) -> str:
    branch = _clean_slot_text(value.strip().strip("%")).strip("/")
    return f"%/{branch}/%" if branch else value


def _normalize_like_value(value: str) -> str:
    cleaned = _clean_slot_text(value.strip().strip("%")).rstrip("下里内")
    return _like_value(cleaned) if cleaned else value


def _normalize_compound_building_address(address: str) -> str:
    cleaned = _clean_slot_text(address)
    if not cleaned:
        return cleaned
    match = re.fullmatch(r"(.+?)([0-9]+)号(?!(?:楼|栋|幢))", cleaned)
    if match:
        return f"{match.group(1)}{match.group(2)}号楼"
    return cleaned


def _derive_address_like_slots(slots: dict[str, Any], raw_value: str) -> None:
    address = _normalize_compound_building_address(
        _clean_slot_text(raw_value.strip().strip("%")).rstrip("下里内")
    )
    if not address:
        return
    parsed = parse_room_address(address)
    if parsed is not None:
        likes = build_room_name_path_likes(parsed)
        if likes:
            slots["address_like"] = _like_value(address)
            slots["address_segment_like"] = likes[0]
            if len(likes) > 1:
                slots["address_alias_segment_like"] = likes[1]
            if len(likes) > 2:
                slots["address_second_alias_segment_like"] = likes[2]
            if len(likes) > 3:
                slots["address_broad_like"] = likes[3]
            for area in _community_area_variants_for_slots(parsed.community_area):
                slots.setdefault("address_area_like", _like_value(area))
            slots["room_no"] = parsed.room
            if parsed.building:
                slots["address_building_name"] = parsed.building
            return
    patterns = _address_segment_like_patterns(address)
    building_names, area_prefix = _address_building_parts(address)
    slots["address_like"] = _like_value(address)
    slots["address_segment_like"] = patterns[0]
    if len(patterns) > 1:
        slots["address_alias_segment_like"] = patterns[1]
    if len(patterns) > 2:
        slots["address_second_alias_segment_like"] = patterns[2]
    if building_names:
        slots["address_building_name"] = building_names[0]
    if len(building_names) > 1:
        slots["address_building_alias_name"] = building_names[1]
    if len(building_names) > 2:
        slots["address_building_second_alias_name"] = building_names[2]
    if area_prefix:
        slots["address_area_like"] = _like_value(area_prefix)
    if _safe_for_broad_address_like(address):
        slots["address_broad_like"] = _like_value(address)


def _community_area_variants_for_slots(area: str) -> list[str]:
    cleaned = area.strip()
    if not cleaned:
        return []
    variants = [cleaned]
    if cleaned.startswith("马连道") and len(cleaned) > len("马连道"):
        variants.append(cleaned[len("马连道") :])
    return list(dict.fromkeys(item for item in variants if item))


def _address_building_parts(address: str) -> tuple[list[str], str | None]:
    if parse_room_address(address) is not None:
        return [], None
    cleaned = _clean_slot_text(address)
    if match := re.fullmatch(r"([0-9]+(?:号)?(?:楼|栋)?)", cleaned):
        return _building_name_aliases(match.group(1)), None
    if match := re.fullmatch(r"(.+?)([0-9]+(?:号)?(?:楼|栋)?)", cleaned):
        return _building_name_aliases(match.group(2)), _normalize_area_prefix(match.group(1))
    return [], None


def _building_name_aliases(building_text: str) -> list[str]:
    match = re.match(r"([0-9]+)", building_text)
    if not match:
        return [building_text] if building_text else []
    number = match.group(1)
    canonical = f"{number}号楼"
    aliases = [building_text, canonical, number, f"{number}栋"]
    if building_text.endswith("号") and not building_text.endswith(("号楼", "栋", "幢")):
        aliases = [canonical, building_text, number, f"{number}栋"]
    return list(dict.fromkeys(aliases))


def _normalize_area_prefix(prefix: str) -> str | None:
    cleaned = _clean_slot_text(prefix).strip("/")
    for suffix in ("社区", "小区", "园区"):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned or None


def _address_segment_like_patterns(address: str) -> list[str]:
    cleaned = _clean_slot_text(address)
    patterns = [_path_segment_like(cleaned)]
    if match := re.fullmatch(r"([0-9]+)(?:号)?(?:楼|栋)?", cleaned):
        number = match.group(1)
        patterns.extend(_path_segment_like(value) for value in [number, f"{number}号楼", f"{number}栋"])
    elif match := re.search(r"(.+?)([0-9]+)(?:号)?(?:楼|栋)?$", cleaned):
        prefix = match.group(1)
        number = match.group(2)
        patterns.extend(
            [
                _contextual_path_segment_like(prefix, number),
                _contextual_path_segment_like(prefix, f"{number}号楼"),
                _contextual_path_segment_like(prefix, f"{number}栋"),
            ]
        )
    return list(dict.fromkeys(item for item in patterns if item))


def _path_segment_like(value: str) -> str:
    return f"%/{value}/%"


def _contextual_path_segment_like(prefix: str, value: str) -> str:
    return f"%/{prefix}%/{value}/%"


def _safe_for_broad_address_like(address: str) -> bool:
    return not re.fullmatch(r"[0-9]+(?:号)?(?:楼|栋)?", address) and len(address) >= 4


def _confidence_from_distance(distance: float) -> float:
    return round(max(0.0, min(0.99, 1.0 - distance)), 2)


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
