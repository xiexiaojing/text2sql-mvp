from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .conversation_context import (
    COUNT_QUESTION_MARKERS,
    contains_domain_subject,
    find_prior_user_with_markers,
    is_list_detail_follow_up,
    is_prior_count_context,
    looks_like_follow_up,
    normalize_history,
    normalize_question,
    previous_user_question,
    strip_follow_up_prefixes,
    subject_from_history,
)
from .visualization import CHART_TYPE_KEYWORDS, CHART_TYPE_LABELS, detect_requested_chart_type

DIMENSION_IN_PRIOR_RE = re.compile(
    r"按([\u4e00-\u9fffA-Za-z0-9]+?)(?=统计|分布|排名|分组|占比|$)"
)

GROUP_DIMENSIONS = ("状态", "渠道", "来源", "分类", "类别", "商户", "金额", "性别", "类型")

COUNT_QUESTION_SUFFIX_RE = re.compile(
    r"(?:一共多少人|多少人|有多少|多少名|数量|人数|一共多少)$"
)

_TOPIC_PATTERN = re.compile(
    r"([\u4e00-\u9fff\dA-Za-z]+(?:渠道|状态|金额|退款|商户)?"
    r"(?:分布|统计|排名|占比|结构|构成|情况|趋势))"
)


@dataclass(frozen=True)
class FollowUpRewrite:
    effective_question: str
    reason: str


@dataclass(frozen=True)
class FollowUpRule:
    id: str
    priority: int
    rewrite: Callable[[str, list[dict[str, str]]], FollowUpRewrite | None]


def apply_follow_up_rewrites(
    question: str,
    history: list[dict[str, Any]] | None,
) -> tuple[str, dict[str, Any] | None]:
    current = normalize_question(question)
    normalized_history = normalize_history(history)
    if not normalized_history:
        return current, None

    for rule in _RULES:
        result = rule.rewrite(current, normalized_history)
        if result and result.effective_question != current:
            return result.effective_question, {
                "kind": "conversation",
                "status": "ok",
                "originalQuestion": question,
                "effectiveQuestion": result.effective_question,
                "history": normalized_history[-4:],
                "rewriteReason": result.reason,
            }

    return current, None


def _extract_requested_dimension(question: str) -> str | None:
    normalized = strip_follow_up_prefixes(normalize_question(question))
    if "换成" in normalized:
        normalized = normalized.replace("换成", "按")
    if "改成" in normalized:
        normalized = normalized.replace("改成", "按")

    explicit = re.match(r"^按([\u4e00-\u9fffA-Za-z0-9]+)(?:统计|分布|排名|分组)?$", normalized)
    if explicit:
        return explicit.group(1)

    for dimension in GROUP_DIMENSIONS:
        if dimension in normalized and len(normalized) <= len(dimension) + 4:
            return dimension
    return None


def _apply_dimension_to_prior(prior: str, new_dimension: str) -> str:
    match = DIMENSION_IN_PRIOR_RE.search(prior)
    if match:
        return prior[: match.start(1)] + new_dimension + prior[match.end(1) :]

    base = prior.rstrip("？?。.!！").strip()
    for suffix in ("总数", "有多少", "数量", "笔数", "合计"):
        if base.endswith(suffix):
            base = base[: -len(suffix)].strip()
            break
    if not base:
        base = prior.rstrip("？?。.!！").strip()
    return f"{base}按{new_dimension}统计"


def _rewrite_dimension_slot_follow_up(question: str, history: list[dict[str, str]]) -> FollowUpRewrite | None:
    if not looks_like_follow_up(question):
        return None
    new_dimension = _extract_requested_dimension(question)
    if not new_dimension:
        return None

    prior = previous_user_question(history, exclude=question)
    if not prior:
        return None

    if DIMENSION_IN_PRIOR_RE.search(prior):
        rewritten = _apply_dimension_to_prior(prior, new_dimension)
    elif contains_domain_subject(question):
        rewritten = _apply_dimension_to_prior(
            f"{strip_follow_up_prefixes(question)}按{new_dimension}统计",
            new_dimension,
        )
    else:
        subject = subject_from_history(history)
        if subject:
            rewritten = f"{subject}按{new_dimension}统计"
        else:
            rewritten = _apply_dimension_to_prior(prior, new_dimension)

    if rewritten in {question, prior}:
        return None
    return FollowUpRewrite(rewritten, "dimension_slot_follow_up")


def count_question_to_list_question(count_question: str) -> str:
    normalized = normalize_question(count_question).rstrip("？?。.!！")
    if COUNT_QUESTION_SUFFIX_RE.search(normalized):
        base = COUNT_QUESTION_SUFFIX_RE.sub("", normalized).strip()
        if base:
            return f"{base}有哪些"
    return normalized


def _rewrite_count_to_list_follow_up(question: str, history: list[dict[str, str]]) -> FollowUpRewrite | None:
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
    rewritten = count_question_to_list_question(subject_question)
    if rewritten in {subject_question, question}:
        return None
    return FollowUpRewrite(rewritten, "count_to_list_follow_up")


def _all_chart_keywords() -> tuple[str, ...]:
    keywords: list[str] = []
    for _, chart_keywords in CHART_TYPE_KEYWORDS:
        keywords.extend(chart_keywords)
    return tuple(keywords)


def _strip_chart_keywords(text: str) -> str:
    stripped = text
    for keyword in _all_chart_keywords():
        stripped = stripped.replace(keyword, "")
    return re.sub(r"\s+", " ", stripped).strip()


def _extract_chart_topic(text: str) -> str | None:
    normalized = normalize_question(text)
    if not normalized:
        return None
    chart_pattern = "|".join(
        re.escape(keyword) for keyword in sorted(_all_chart_keywords(), key=len, reverse=True)
    )

    use_show = re.search(
        rf"用(?:{chart_pattern})展示(.+?(?:分布|统计|排名|占比|结构|构成|情况|趋势))",
        normalized,
    )
    if use_show:
        topic = use_show.group(1).strip(" 、，,")
        if len(topic) >= 2:
            return topic

    before_chart = re.search(
        rf"(?:生成(?:一份)?|用|展示|统计|查询|看看)(?:一份)?(.+?)(?:{chart_pattern})",
        normalized,
    )
    if before_chart:
        topic = before_chart.group(1).strip(" 、，,")
        if len(topic) >= 2 and not any(keyword in topic for keyword in _all_chart_keywords()):
            return topic

    after_show = re.search(
        r"(?:展示|统计|查询|看看)(.+?(?:分布|统计|排名|占比|结构|构成|情况|趋势))",
        normalized,
    )
    if after_show:
        topic = after_show.group(1).strip(" 、，,")
        if len(topic) >= 2 and not any(keyword in topic for keyword in _all_chart_keywords()):
            return topic

    topic_match = _TOPIC_PATTERN.search(normalized)
    if topic_match:
        topic = topic_match.group(1).strip()
        if (
            len(topic) >= 2
            and not any(keyword in topic for keyword in _all_chart_keywords())
            and not re.match(r"^[用按以向为对]", topic)
        ):
            return topic
    return None


def _extract_chart_topic_from_assistant(content: str) -> str | None:
    text = str(content or "").strip()
    if not text:
        return None
    labels = "|".join(
        re.escape(label) for label in sorted(set(CHART_TYPE_LABELS.values()), key=len, reverse=True)
    )
    display_match = re.search(
        rf"(?:已按您要求的|当前数据更适合)(?:{labels})展示[：:]\s*(.+?)(?:合计|，|,|。|\n|$)",
        text,
    )
    if display_match:
        topic = display_match.group(1).strip()
        if len(topic) >= 2:
            return topic
    heading_match = re.search(r"^#{1,3}\s*(.+?(?:分布|统计|排名|占比|趋势))", text, flags=re.MULTILINE)
    if heading_match:
        return heading_match.group(1).strip()
    inline_topic = _TOPIC_PATTERN.search(text)
    if inline_topic and re.search(r"合计|统计如下|分布|走势", text):
        return inline_topic.group(1).strip()
    return None


def _is_bare_chart_type_follow_up(question: str) -> bool:
    if detect_requested_chart_type(question) is None:
        return False
    if _extract_chart_topic(question):
        return False
    remainder = re.sub(r"[也再一下生成展示用按的换成改为了来]", "", _strip_chart_keywords(question))
    return len(remainder) <= 4


def _find_prior_chart_topic(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = normalize_question(exclude)
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = normalize_question(str(item.get("content", "")))
        if not content or content == exclude_norm:
            continue
        topic = _extract_chart_topic(str(item.get("content", "")))
        if topic:
            return topic
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        topic = _extract_chart_topic_from_assistant(str(item.get("content", "")))
        if topic:
            return topic
    return None


def _find_prior_chart_question(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = normalize_question(exclude)
    last_qualified: str | None = None
    for item in history:
        if item.get("role") != "user":
            continue
        content = normalize_question(str(item.get("content", "")))
        if not content or content == exclude_norm:
            continue
        raw = str(item.get("content", ""))
        if (
            _extract_chart_topic(raw)
            or (
                detect_requested_chart_type(raw)
                and (
                    contains_domain_subject(raw)
                    or any(marker in raw for marker in ("分布", "统计", "排名", "占比", "趋势"))
                )
            )
        ):
            last_qualified = raw
    if last_qualified and _extract_chart_topic(last_qualified):
        return last_qualified
    return previous_user_question(history, exclude)


def _chart_keywords(chart_type: str) -> tuple[str, ...]:
    for current_type, keywords in CHART_TYPE_KEYWORDS:
        if current_type == chart_type:
            return keywords
    return ()


def _preferred_chart_label(question: str, chart_type: str) -> str:
    best = ""
    for current_type, keywords in CHART_TYPE_KEYWORDS:
        if current_type != chart_type:
            continue
        for keyword in keywords:
            if keyword in question and len(keyword) > len(best):
                best = keyword
    return best or CHART_TYPE_LABELS.get(chart_type, "")


def _rewrite_chart_type_follow_up(question: str, history: list[dict[str, str]]) -> FollowUpRewrite | None:
    requested = detect_requested_chart_type(question)
    if requested is None:
        return None
    if not looks_like_follow_up(question) and not _is_bare_chart_type_follow_up(question):
        return None

    new_label = _preferred_chart_label(question, requested)
    if not new_label:
        return None

    topic = _find_prior_chart_topic(history, exclude=question)
    if topic:
        return FollowUpRewrite(f"生成一份{topic}{new_label}", "chart_type_follow_up")

    previous = _find_prior_chart_question(history, exclude=question)
    if not previous:
        return None

    previous_chart = detect_requested_chart_type(previous)
    if previous_chart is not None:
        for keyword in sorted(_chart_keywords(previous_chart), key=len, reverse=True):
            if keyword in previous:
                return FollowUpRewrite(previous.replace(keyword, new_label, 1), "chart_type_follow_up")

    merged = _strip_chart_keywords(previous)
    merged = re.sub(r"\s+", " ", merged).strip(" ，,、")
    if new_label not in merged:
        merged = f"{merged}{new_label}"
    return FollowUpRewrite(merged.strip(), "chart_type_follow_up")


def _build_rules() -> tuple[FollowUpRule, ...]:
    rules = [
        FollowUpRule("chart_type_follow_up", 100, _rewrite_chart_type_follow_up),
        FollowUpRule("count_to_list_follow_up", 91, _rewrite_count_to_list_follow_up),
        FollowUpRule("dimension_slot_follow_up", 10, _rewrite_dimension_slot_follow_up),
    ]
    return tuple(sorted(rules, key=lambda rule: -rule.priority))


_RULES = _build_rules()
