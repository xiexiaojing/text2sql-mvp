from __future__ import annotations

from .conversation_context import (
    COUNT_ANSWER_MARKERS,
    COUNT_QUESTION_MARKERS,
    assistant_answer_matches,
    find_prior_user_with_markers,
    is_list_detail_follow_up,
    is_prior_count_context,
    normalize_question,
    previous_user_question,
)
from .conversation_rewrite import count_question_to_list_question


def rewrite_list_detail_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    if not is_list_detail_follow_up(question):
        return None
    if not is_prior_count_context(history, exclude=question):
        return None
    subject_question = find_prior_user_with_markers(
        history,
        exclude=question,
        markers=COUNT_QUESTION_MARKERS,
    )
    if not subject_question:
        return None
    rewritten = _count_question_to_list_question(subject_question)
    if rewritten in {subject_question, question}:
        return None
    return rewritten


__all__ = [
    "COUNT_ANSWER_MARKERS",
    "COUNT_QUESTION_MARKERS",
    "rewrite_list_detail_follow_up",
]
