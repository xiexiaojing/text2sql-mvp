from __future__ import annotations

import re

LIST_DETAIL_FOLLOW_UPS = frozenset(
    {
        "是谁",
        "有哪些",
        "名单",
        "分别是谁",
        "都有谁",
        "哪几个",
    }
)

COUNT_QUESTION_MARKERS = ("有多少人", "多少人", "一共多少", "多少名", "数量", "人数")

COUNT_SUBJECT_LIST_QUESTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("网格员",), "社区网格员有哪些"),
)

COUNT_ANSWER_MARKERS = ("共有", "共 ", "人数为", "名为", "名网格员", "名社工", "名党员", "位居民")


def rewrite_list_detail_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = _normalize_question(question).rstrip("？?。.!！")
    if not _is_list_detail_follow_up(normalized):
        return None
    if not _is_prior_count_context(history, exclude=question):
        return None
    subject_question = _find_count_subject_question(history, exclude=question)
    if not subject_question:
        return None
    for keywords, list_question in COUNT_SUBJECT_LIST_QUESTIONS:
        if any(keyword in subject_question for keyword in keywords):
            return list_question
    return None


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _is_list_detail_follow_up(question: str) -> bool:
    if question in LIST_DETAIL_FOLLOW_UPS:
        return True
    if question.endswith("分别是谁") and len(question) <= 12:
        return True
    if question.endswith("都有谁") and len(question) <= 10:
        return True
    return False


def _previous_user_question(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = _normalize_question(str(item.get("content", "")))
        if content and content != exclude_norm:
            return str(item.get("content", "")).strip()
    return None


def _is_prior_count_context(history: list[dict[str, str]], *, exclude: str) -> bool:
    previous_user = _previous_user_question(history, exclude)
    if previous_user and any(marker in previous_user for marker in COUNT_QUESTION_MARKERS):
        return True
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content", ""))
        return any(marker in content for marker in COUNT_ANSWER_MARKERS)
    return False


def _find_count_subject_question(history: list[dict[str, str]], *, exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", "")).strip()
        normalized = _normalize_question(content)
        if not normalized or normalized == exclude_norm:
            continue
        if any(marker in normalized for marker in COUNT_QUESTION_MARKERS):
            return content
    return None
