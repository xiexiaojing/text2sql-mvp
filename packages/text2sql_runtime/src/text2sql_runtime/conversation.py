from __future__ import annotations

from typing import Any

from .conversation_context import compact, normalize_history
from .conversation_rewrite import apply_follow_up_rewrites


def contextualize_question(question: str, history: list[dict[str, Any]] | None) -> tuple[str, dict[str, Any] | None]:
    return apply_follow_up_rewrites(question, history)


def conversation_context_lines(history: list[dict[str, Any]] | None) -> list[str]:
    normalized_history = normalize_history(history, max_turns=6)
    if not normalized_history:
        return []
    lines = [
        "Recent conversation context:",
        "Use this only to resolve follow-up references or omitted subjects; the SQL must answer the current question.",
    ]
    for item in normalized_history:
        role = "user" if item["role"] == "user" else "assistant"
        lines.append(f"- {role}: {item['content']}")
    return lines


__all__ = ["contextualize_question", "conversation_context_lines", "compact", "normalize_history"]
