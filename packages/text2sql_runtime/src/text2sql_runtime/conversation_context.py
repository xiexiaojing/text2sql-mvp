from __future__ import annotations

import re
from typing import Any

FOLLOW_UP_PREFIXES = ("那", "这个", "那个", "刚才", "上面", "继续", "再", "也")

FOLLOW_UP_MARKERS = (
    *FOLLOW_UP_PREFIXES,
    "呢",
    "换成",
    "改成",
)

STANDALONE_QUERY_MARKERS = (
    "包括哪几个",
    "包括哪些",
    "有哪些",
    "有多少",
    "多少",
    "TOP",
    "top",
    "是谁",
    "姓名",
    "手机",
    "名单",
    "列表",
    "明细",
    "详情",
    "占比",
    "比例",
    "排名",
    "笔数",
    "几笔",
)

COUNT_QUESTION_MARKERS = (
    "有多少",
    "多少人",
    "多少笔",
    "一共多少",
    "多少名",
    "数量",
    "人数",
    "总数",
)

COUNT_ANSWER_MARKERS = ("共有", "共 ", "笔。", "订单共", "商户共", "退款共")

LIST_DETAIL_MARKERS = frozenset(
    {
        "是谁",
        "有哪些",
        "名单",
        "分别是谁",
        "都有谁",
        "哪几个",
    }
)

DOMAIN_SUBJECTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("payment_order", "支付订单", ("支付订单", "订单", "交易订单")),
    ("refund_order", "退款", ("退款", "退款订单", "退单")),
    ("merchant", "商户", ("商户", "商家", "门店")),
)


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def compact(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip())[:600]


def normalize_history(history: list[dict[str, Any]] | None, *, max_turns: int = 8) -> list[dict[str, str]]:
    if not history:
        return []
    normalized: list[dict[str, str]] = []
    for item in history[-max_turns:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": compact(content)})
    return normalized


def strip_follow_up_prefixes(question: str) -> str:
    rewritten = question.strip()
    for prefix in FOLLOW_UP_PREFIXES:
        if rewritten.startswith(prefix):
            rewritten = rewritten[len(prefix) :].strip()
    return rewritten.rstrip("？?。.!！").rstrip("呢").strip()


def looks_like_follow_up(question: str) -> bool:
    if any(marker in question for marker in STANDALONE_QUERY_MARKERS):
        return False
    if any(question.startswith(prefix) for prefix in FOLLOW_UP_PREFIXES):
        return True
    if "换成" in question or "改成" in question:
        return True
    if question.endswith("呢") and len(question) <= 10:
        return True
    return any(marker in question for marker in FOLLOW_UP_MARKERS)


def is_list_detail_follow_up(question: str) -> bool:
    normalized = strip_follow_up_prefixes(normalize_question(question))
    if normalized in LIST_DETAIL_MARKERS:
        return True
    if normalized.endswith("分别是谁") and len(normalized) <= 12:
        return True
    if normalized.endswith("都有谁") and len(normalized) <= 10:
        return True
    return False


def last_user_message(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item.get("role") == "user":
            return str(item.get("content", "")).strip()
    return None


def previous_user_question(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = normalize_question(str(item.get("content", "")))
        if content and content != exclude_norm:
            return str(item.get("content", "")).strip()
    return None


def find_prior_user_with_markers(
    history: list[dict[str, str]],
    *,
    exclude: str,
    markers: tuple[str, ...],
    require_all: tuple[str, ...] = (),
) -> str | None:
    exclude_norm = normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", "")).strip()
        normalized = normalize_question(content)
        if not normalized or normalized == exclude_norm:
            continue
        if require_all and not all(marker in normalized for marker in require_all):
            continue
        if any(marker in normalized for marker in markers):
            return content
    return None


def assistant_answer_matches(history: list[dict[str, str]], markers: tuple[str, ...]) -> bool:
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content", ""))
        return any(marker in content for marker in markers)
    return False


def is_prior_count_context(history: list[dict[str, str]], *, exclude: str) -> bool:
    previous_user = previous_user_question(history, exclude)
    if previous_user and any(marker in previous_user for marker in COUNT_QUESTION_MARKERS):
        return True
    return assistant_answer_matches(history, COUNT_ANSWER_MARKERS)


def contains_domain_subject(question: str) -> bool:
    return any(keyword in question for _, _, keywords in DOMAIN_SUBJECTS for keyword in keywords)


def subject_from_history(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", ""))
        for _, subject, keywords in DOMAIN_SUBJECTS:
            if any(keyword in content for keyword in keywords):
                return subject
    return None
