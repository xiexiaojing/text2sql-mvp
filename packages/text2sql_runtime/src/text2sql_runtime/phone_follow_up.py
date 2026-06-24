from __future__ import annotations

import re

PHONE_RE = re.compile(r"(?<!\d)(1\d{10})(?!\d)")

PHONE_COUNT_ANSWER_RE = re.compile(r"手机号为\s*(1\d{10})\s*的居民共有")

PHONE_DETAIL_MARKERS = (
    "具体信息",
    "详细信息",
    "详情",
    "明细",
    "名单",
    "列表",
    "是谁",
    "有哪些",
    "都有谁",
    "哪几个",
)


def rewrite_phone_detail_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = _normalize_question(question).rstrip("？?。.!！")
    if not _is_phone_detail_follow_up(normalized):
        return None
    if not _is_prior_phone_lookup_context(history, exclude=question):
        return None
    phone = _extract_phone_from_history(history)
    if not phone:
        return None
    return f"查询{phone}手机号的居民详细信息"


def question_wants_phone_detail(question: str) -> bool:
    normalized = _normalize_question(question)
    if not normalized:
        return False
    if re.search(r"重复|没留|未留|缺失|未填|导出", normalized):
        return False
    if any(marker in normalized for marker in PHONE_DETAIL_MARKERS):
        return True
    if re.search(r"给出.*(?:信息|详情|明细)", normalized):
        return True
    if "信息" in normalized and re.search(r"手机号|手机号码", normalized):
        return True
    return False


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _is_phone_detail_follow_up(question: str) -> bool:
    if any(marker in question for marker in PHONE_DETAIL_MARKERS):
        return True
    if re.search(r"给出.*(?:信息|详情|明细)", question):
        return True
    return False


def _is_prior_phone_lookup_context(history: list[dict[str, str]], *, exclude: str) -> bool:
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = _normalize_question(str(item.get("content", "")))
        if not content or content == exclude_norm:
            continue
        if PHONE_RE.search(content) and re.search(r"手机号|手机号码|电话", content):
            return True
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        if PHONE_COUNT_ANSWER_RE.search(str(item.get("content", ""))):
            return True
    return False


def _extract_phone_from_history(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        match = PHONE_COUNT_ANSWER_RE.search(str(item.get("content", "")))
        if match:
            return match.group(1)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", ""))
        if not re.search(r"手机号|手机号码|电话", content):
            continue
        match = PHONE_RE.search(content)
        if match:
            return match.group(1)
    return None
