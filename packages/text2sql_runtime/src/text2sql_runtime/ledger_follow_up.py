from __future__ import annotations

import re

LEDGER_LAST_UPDATE_QUESTION_MARKERS = ("最后更新", "更新时间", "台账最后更新")
LEDGER_LAST_UPDATE_ANSWER_MARKERS = ("最后更新时间为", "最后更新时间")

WHO_UPDATED_QUESTION_RE = re.compile(
    r"^(?:那)?谁(?:更新(?:的)?|操作(?:的)?)[？?]?$"
    r"|^(?:那)?更新的谁[？?]?$"
    r"|^更新人(?:是谁)?[？?]?$"
)


def rewrite_ledger_updater_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = _normalize_question(question).rstrip("？?。.!！")
    if not WHO_UPDATED_QUESTION_RE.match(normalized):
        return None
    if not _is_ledger_last_update_context(history, exclude=question):
        return None
    prior = _find_prior_ledger_last_update_question(history, exclude=question)
    if not prior:
        return None
    rewritten = _rewrite_ledger_updater_question(prior)
    return rewritten if rewritten != prior else None


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _rewrite_ledger_updater_question(prior_question: str) -> str:
    question = prior_question.strip().rstrip("？?。.!！")
    rewritten = re.sub(r"(最后更新|更新时间)(是)?什么时候$", "是谁更新的", question)
    rewritten = re.sub(r"(最后更新|更新时间)$", "是谁更新的", rewritten)
    if "是谁更新的" not in rewritten and "谁更新" not in rewritten:
        if rewritten.endswith("台账"):
            rewritten = f"{rewritten}是谁更新的"
        else:
            rewritten = f"{rewritten}是谁更新的"
    return rewritten


def _is_ledger_last_update_context(history: list[dict[str, str]], *, exclude: str) -> bool:
    prior = _find_prior_ledger_last_update_question(history, exclude=exclude)
    if prior:
        return True
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content", ""))
        return any(marker in content for marker in LEDGER_LAST_UPDATE_ANSWER_MARKERS)
    return False


def _find_prior_ledger_last_update_question(history: list[dict[str, str]], *, exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", "")).strip()
        normalized = _normalize_question(content)
        if not normalized or normalized == exclude_norm:
            continue
        if "台账" in normalized and any(marker in normalized for marker in LEDGER_LAST_UPDATE_QUESTION_MARKERS):
            return content
    return None
