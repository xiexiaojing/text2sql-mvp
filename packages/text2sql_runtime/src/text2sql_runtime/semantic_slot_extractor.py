from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import LlmSettings


@dataclass(frozen=True)
class SlotExtractionResult:
    decision: str
    intent_id: str | None = None
    confidence: float = 0.0
    slots: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    source: str = "llm"
    elapsed_ms: int = 0
    raw_response: str | None = None


class LlmSlotExtractor:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def extract(
        self,
        question: str,
        candidates: Sequence[Mapping[str, Any]],
        history: list[dict[str, object]] | None = None,
    ) -> SlotExtractionResult | None:
        if not self.settings.configured:
            return None
        candidate_map = {_string(candidate.get("id")): candidate for candidate in candidates}
        if not candidate_map:
            return None
        system_prompt = _system_prompt()
        user_prompt = _user_prompt(question, candidates, history)
        started = time.monotonic()
        try:
            if self.settings.transport == "anthropic":
                content = self._generate_with_anthropic(system_prompt, user_prompt)
            else:
                content = self._generate_with_openai(system_prompt, user_prompt)
            payload = _parse_json(content)
        except Exception:
            return None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        decision = _string(payload.get("decision")).lower()
        if decision not in {"select", "fallback"}:
            return None
        intent_id = _string(payload.get("intent_id")) or None
        if decision == "select" and (not intent_id or intent_id not in candidate_map):
            return None
        slots = payload.get("slots")
        allowed_slots = _allowed_slots(candidate_map.get(intent_id, {})) if intent_id else set()
        return SlotExtractionResult(
            decision=decision,
            intent_id=intent_id,
            confidence=_confidence(payload.get("confidence")),
            slots=_filtered_slots(slots, allowed_slots),
            reason=_string(payload.get("reason")) or None,
            source="llm",
            elapsed_ms=elapsed_ms,
            raw_response=content,
        )

    def _generate_with_openai(self, system_prompt: str, user_prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.settings.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.settings.model,
            "temperature": 0,
            "max_tokens": min(self.settings.max_tokens, 700),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = httpx.post(
            self.settings.base_url,
            headers=headers,
            json=payload,
            timeout=self.settings.timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            raise ValueError("LLM slot response missing content")
        return str(content)

    def _generate_with_anthropic(self, system_prompt: str, user_prompt: str) -> str:
        try:
            import anthropic
        except ModuleNotFoundError as exc:
            raise RuntimeError("anthropic sdk is not installed") from exc
        client = anthropic.Anthropic(
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout_seconds,
        )
        response = client.messages.create(
            model=self.settings.model,
            max_tokens=min(self.settings.max_tokens, 700),
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        chunks: list[str] = []
        for block in response.content:
            if getattr(block, "type", "") == "text":
                chunks.append(str(getattr(block, "text", "")))
        content = "\n".join(chunks).strip()
        if not content:
            raise ValueError("LLM slot response missing content")
        return content


def _system_prompt() -> str:
    return (
        "你是业务 Text-to-SQL 的语义规划器。只返回 JSON，不要解释。"
        "你的任务是在 candidate_intents 中选择一个最匹配用户问题的 intent，并从用户问题中抽取槽位。"
        "禁止选择候选之外的 intent，禁止编写 SQL，禁止猜测问题中没有提供的业务事实。"
        "如果候选都不能覆盖问题，返回 decision=fallback。"
        "JSON 格式：{\"decision\":\"select|fallback\",\"intent_id\":\"...\","
        "\"confidence\":0.0,\"slots\":{},\"reason\":\"...\"}。"
    )


def _user_prompt(
    question: str,
    candidates: Sequence[Mapping[str, Any]],
    history: list[dict[str, object]] | None,
) -> str:
    payload = {
        "current_user": question,
        "history": _compact_history(history or []),
        "candidate_intents": [_candidate_projection(candidate) for candidate in candidates],
        "slot_rules": [
            "只填 candidate.allowed_slots 中出现的槽位。",
            "person_name 使用中文姓名原文。",
            "role_like/tag_like/address_like 需要包含 % 模糊匹配符。",
            "未出现的 required slot 不要编造，留空即可。",
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _candidate_projection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": _string(candidate.get("id")),
        "display_name": _string(candidate.get("display_name")),
        "status": _string(candidate.get("status")),
        "output_type": _string(candidate.get("output_type")),
        "required_slots": list(candidate.get("required_slots") or []),
        "optional_slots": list(candidate.get("optional_slots") or []),
        "slot_defaults": dict(candidate.get("slot_defaults") or {}),
        "allowed_slots": sorted(_allowed_slots(candidate)),
        "examples": list(candidate.get("examples") or [])[:4],
        "matched_query": _string(candidate.get("matched_query")),
        "distance": candidate.get("distance"),
        "reason": _string(candidate.get("reason")),
    }
    return {key: value for key, value in result.items() if value not in ("", [], {}, None)}


def _compact_history(history: list[dict[str, object]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in history[-4:]:
        role = _string(item.get("role"))
        content = _string(item.get("content"))
        if role and content:
            result.append({"role": role, "content": content[:300]})
    return result


def _parse_json(content: str) -> Mapping[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    payload = json.loads(stripped)
    if not isinstance(payload, Mapping):
        raise ValueError("slot response is not a JSON object")
    return payload


def _allowed_slots(candidate: Mapping[str, Any]) -> set[str]:
    slots: set[str] = set()
    slots.update(str(slot) for slot in candidate.get("required_slots") or [])
    slots.update(str(slot) for slot in candidate.get("optional_slots") or [])
    slots.update(str(slot) for slot in dict(candidate.get("slot_defaults") or {}))
    slots.update(_derived_slot_names(slots))
    return slots


def _derived_slot_names(slots: set[str]) -> set[str]:
    derived: set[str] = set()
    if "role" in slots:
        derived.add("role_like")
    if "role_like" in slots:
        derived.add("role")
    if "tag_name" in slots:
        derived.add("tag_like")
    if "merchant_name" in slots:
        derived.add("merchant_name_like")
    if "area_name" in slots:
        derived.add("area_like")
    if "category" in slots:
        derived.add("category_like")
    if "field_name" in slots:
        derived.add("field_like")
    if "skill" in slots:
        derived.add("skill_like")
    if "skill_like" in slots:
        derived.add("skill")
    if "grid_name" in slots:
        derived.add("grid_name_like")
    if "address" in slots or "address_like" in slots:
        derived.add("address_like")
        derived.add("address_segment_like")
        derived.add("address_alias_segment_like")
        derived.add("address_second_alias_segment_like")
        derived.add("address_building_name")
        derived.add("address_building_alias_name")
        derived.add("address_building_second_alias_name")
        derived.add("address_area_like")
        derived.add("address_broad_like")
    if "age" in slots:
        derived.add("age_cutoff_ms")
    return derived


def _filtered_slots(value: Any, allowed_slots: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key, raw_value in value.items():
        slot = _string(key)
        if slot not in allowed_slots:
            continue
        if raw_value in (None, "", [], {}):
            continue
        result[slot] = raw_value
    return result


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _string(value: Any) -> str:
    return str(value or "").strip()
