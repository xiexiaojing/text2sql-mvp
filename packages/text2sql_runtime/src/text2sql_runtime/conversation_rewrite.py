from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
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

OPTIONAL_BLOCK_RE = re.compile(r"\[\[\s*([A-Za-z_][A-Za-z0-9_]*)\s*:(.*?)\]\]", re.DOTALL)
GROUP_BY_COLUMN_RE = re.compile(
    r"\bGROUP\s+BY\s+(?:[A-Za-z_][A-Za-z0-9_]*\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
DIMENSION_OPERATED_RE = re.compile(
    r"^(?:按|按照|以|换成|改成|改为|换为)(?:按|按照|以)?"
    r"(?P<dimension>[\u4e00-\u9fffA-Za-z0-9_]+)"
    r"(?:统计|分布|排名|分组|汇总|占比)?$"
)
BARE_DIMENSION_RE = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_]{1,16}$")

NON_DIMENSION_FOLLOW_UPS = frozenset(
    {
        "数量",
        "总数",
        "合计",
        "明细",
        "详情",
        "列表",
        "名单",
        "有哪些",
        "多少",
        "有多少",
        "是谁",
        "这个",
        "那个",
    }
)

VALUE_SUBSTITUTION_CONTEXT_MARKERS = (
    "明细",
    "列表",
    "清单",
    "台账",
    "详情",
    "分布",
    "统计",
    "排名",
    "占比",
    "有哪些",
)

VALUE_TAIL_START_MARKERS = (
    "已",
    "未",
    "在用",
    "闲置",
    "存放",
    "所属",
    "分配",
    "品牌",
    "状态",
    "类型",
    "分类",
    "来源",
    "部门",
    "用户",
    "商户",
    "供应商",
    "订单",
    "退款",
    "项目",
    "采购",
    "询价",
    "会议",
    "知讯",
    "IT资产",
    "资产",
)

STATUS_VALUE_RE = re.compile(
    r"(?:已[^的]{1,6}|未[^的]{1,6}|在用|库存|闲置|正常|停用|报废|待[^的]{1,6}|可[^的]{1,6})$"
)

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
class PriorQuestionAnchor:
    raw_question: str
    effective_question: str
    substitution_value: str | None = None


@dataclass(frozen=True)
class DimensionLexicon:
    aliases: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_aliases(cls, aliases: dict[str, str] | None = None) -> "DimensionLexicon":
        normalized: dict[str, str] = {}
        for alias, canonical in (aliases or {}).items():
            alias_text = _clean_dimension(alias)
            canonical_text = _clean_dimension(canonical)
            if alias_text and canonical_text:
                normalized[alias_text] = canonical_text
        return cls(normalized)

    def resolve(self, value: str, *, allow_unknown: bool) -> str | None:
        candidate = _clean_dimension(value)
        if not candidate or not _looks_like_dimension_phrase(candidate):
            return None
        if not self.aliases:
            return candidate if allow_unknown else None
        if candidate in self.aliases:
            return self.aliases[candidate]
        lowered = candidate.lower()
        for alias, canonical in self.aliases.items():
            if lowered == alias.lower():
                return canonical
        return candidate if allow_unknown else None


@dataclass(frozen=True)
class FollowUpRewriteContext:
    dimensions: DimensionLexicon = field(default_factory=DimensionLexicon)


@dataclass(frozen=True)
class FollowUpRule:
    id: str
    priority: int
    rewrite: Callable[[str, list[dict[str, str]], FollowUpRewriteContext], FollowUpRewrite | None]


def build_follow_up_rewrite_context(
    catalog: Any | None = None,
    business_semantics: Any | None = None,
) -> FollowUpRewriteContext:
    return FollowUpRewriteContext(
        dimensions=DimensionLexicon.from_aliases(
            _dimension_aliases_from_catalog(catalog, business_semantics)
        )
    )


def apply_follow_up_rewrites(
    question: str,
    history: list[dict[str, Any]] | None,
    rewrite_context: FollowUpRewriteContext | None = None,
) -> tuple[str, dict[str, Any] | None]:
    current = normalize_question(question)
    normalized_history = normalize_history(history)
    if not normalized_history:
        return current, None

    context = rewrite_context or FollowUpRewriteContext()
    for rule in _RULES:  # 追问规则
        result = rule.rewrite(current, normalized_history, context)
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


def _dimension_aliases_from_catalog(
    catalog: Any | None,
    business_semantics: Any | None,
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    group_columns = _group_columns_from_templates(business_semantics)
    group_slots = _group_slots_from_templates(business_semantics)

    if catalog is None:
        return aliases

    for table in getattr(catalog, "tables", []):
        table_display = str(getattr(table, "display_name", "") or "")
        columns = getattr(table, "columns", {}) or {}
        for column in columns.values():
            column_name = str(getattr(column, "name", "") or "")
            display = str(getattr(column, "display_name", "") or "")
            if not column_name or (getattr(column, "sensitive", False) or not getattr(column, "searchable", True)):
                continue
            if group_columns and column_name.lower() not in group_columns and column_name not in group_slots:
                continue
            for alias in _column_dimension_aliases(column_name, display, table_display):
                aliases.setdefault(alias, _canonical_dimension_label(display, column_name, table_display))
    return aliases


def _group_columns_from_templates(business_semantics: Any | None) -> set[str]:
    columns: set[str] = set()
    for template in getattr(business_semantics, "templates", {}).values():
        sql = str(getattr(template, "sql", "") or "")
        columns.update(match.group(1).lower() for match in GROUP_BY_COLUMN_RE.finditer(sql))
        for _, body in OPTIONAL_BLOCK_RE.findall(sql):
            columns.update(match.group(1).lower() for match in GROUP_BY_COLUMN_RE.finditer(body))
    return columns


def _group_slots_from_templates(business_semantics: Any | None) -> set[str]:
    slots: set[str] = set()
    for template in getattr(business_semantics, "templates", {}).values():
        sql = str(getattr(template, "sql", "") or "")
        for slot, body in OPTIONAL_BLOCK_RE.findall(sql):
            if GROUP_BY_COLUMN_RE.search(body):
                slots.add(slot)
    return slots


def _column_dimension_aliases(column_name: str, display: str, table_display: str) -> tuple[str, ...]:
    values = [column_name, display]
    if table_display and display.startswith(table_display):
        values.append(display[len(table_display) :])
    values.extend(_display_suffix_aliases(display))
    return tuple(dict.fromkeys(_clean_dimension(value) for value in values if _clean_dimension(value)))


def _display_suffix_aliases(display: str) -> tuple[str, ...]:
    cleaned = _clean_dimension(display)
    if len(cleaned) <= 2:
        return ()
    suffixes = [cleaned[-length:] for length in (2, 3) if len(cleaned) > length]
    return tuple(suffix for suffix in suffixes if re.search(r"[\u4e00-\u9fff]", suffix))


def _canonical_dimension_label(display: str, column_name: str, table_display: str) -> str:
    cleaned_display = _clean_dimension(display)
    if table_display and cleaned_display.startswith(table_display):
        stripped = _clean_dimension(cleaned_display[len(table_display) :])
        if stripped:
            return stripped
    return cleaned_display or column_name


def _extract_requested_dimension(
    question: str,
    rewrite_context: FollowUpRewriteContext,
) -> str | None:
    normalized = strip_follow_up_prefixes(normalize_question(question))
    operated = DIMENSION_OPERATED_RE.match(normalized)
    if operated:
        return rewrite_context.dimensions.resolve(
            operated.group("dimension"),
            allow_unknown=True,
        )
    if BARE_DIMENSION_RE.match(normalized):
        return rewrite_context.dimensions.resolve(normalized, allow_unknown=False)
    return None


def _clean_dimension(value: str) -> str:
    return normalize_question(str(value)).strip(" ，,、：:").rstrip("？?。.!！").rstrip("呢").strip()


def _looks_like_dimension_phrase(value: str) -> bool:
    normalized = _clean_dimension(value)
    if not normalized or normalized in NON_DIMENSION_FOLLOW_UPS:
        return False
    if any(marker in normalized for marker in ("多少", "总数", "数量", "列表", "明细", "详情", "名单")):
        return False
    return True


def _find_prior_question_anchor(
    history: list[dict[str, str]],
    exclude: str,
    rewrite_context: FollowUpRewriteContext,
    *,
    require_markers: tuple[str, ...] = (),
) -> PriorQuestionAnchor | None:
    exclude_norm = normalize_question(exclude)
    for index in range(len(history) - 1, -1, -1):
        item = history[index]
        if item.get("role") != "user":
            continue
        raw_question = str(item.get("content", "")).strip()
        raw_normalized = normalize_question(raw_question)
        if not raw_normalized or raw_normalized == exclude_norm:
            continue

        effective_question = raw_question
        substitution_value: str | None = None
        if looks_like_follow_up(raw_question):
            effective_question, log = apply_follow_up_rewrites(
                raw_question,
                history[:index],
                rewrite_context,
            )
            if log and log.get("rewriteReason") == "value_substitution_follow_up":
                substitution_value = _extract_follow_up_value(raw_question)

        if require_markers and not any(marker in effective_question for marker in require_markers):
            continue
        return PriorQuestionAnchor(
            raw_question=raw_question,
            effective_question=effective_question,
            substitution_value=substitution_value,
        )
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


def _rewrite_dimension_slot_follow_up(
    question: str,
    history: list[dict[str, str]],
    rewrite_context: FollowUpRewriteContext,
) -> FollowUpRewrite | None:
    if not looks_like_follow_up(question):
        return None
    new_dimension = _extract_requested_dimension(question, rewrite_context)
    if not new_dimension:
        return None

    anchor = _find_prior_question_anchor(history, question, rewrite_context)
    if not anchor:
        return None
    prior = anchor.effective_question

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


def _rewrite_count_to_list_follow_up(
    question: str,
    history: list[dict[str, str]],
    rewrite_context: FollowUpRewriteContext,
) -> FollowUpRewrite | None:
    del rewrite_context
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


def _extract_follow_up_value(question: str) -> str | None:
    normalized = strip_follow_up_prefixes(normalize_question(question))
    if not normalized:
        return None
    if normalized.startswith(("按", "按照", "以", "换成", "改成", "改为", "换为")):
        return None
    normalized = normalized.rstrip("的").strip()
    if not normalized:
        return None
    if any(marker in normalized for marker in ("统计", "分布", "排名", "分组", "占比", "明细", "列表")):
        return None
    if len(normalized) > 8:
        return None
    if not re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9_]+", normalized):
        return None
    return normalized


def _leading_value_span(question: str) -> str | None:
    normalized = normalize_question(question)
    if not normalized:
        return None
    positions = [
        normalized.find(marker)
        for marker in VALUE_TAIL_START_MARKERS
        if normalized.find(marker) > 0
    ]
    if positions:
        candidate = normalized[: min(positions)].strip()
        return candidate or None
    return None


def _replace_substitution_value(
    question: str,
    new_value: str,
    prior_value: str | None,
) -> str | None:
    normalized = normalize_question(question)
    prefix, tail = _split_value_question_prefix_and_tail(normalized)
    if not prefix or not tail:
        return None

    candidates = _value_substitution_candidates(prefix)
    if not candidates:
        return None

    new_is_status = _is_status_value(new_value)
    prior_candidate = _clean_dimension(prior_value or "")
    target_index: int | None = None

    if prior_candidate:
        for index, candidate in enumerate(candidates):
            if candidate["text"] == prior_candidate and candidate["kind"] == ("status" if new_is_status else "generic"):
                target_index = index
                break

    if target_index is None:
        if new_is_status:
            for index in range(len(candidates) - 1, -1, -1):
                if candidates[index]["kind"] == "status":
                    target_index = index
                    break
        else:
            for index, candidate in enumerate(candidates):
                if candidate["kind"] != "status":
                    target_index = index
                    break

    if target_index is None:
        target_index = len(candidates) - 1

    rewritten_prefix = "".join(
        new_value if index == target_index else str(candidate["text"])
        for index, candidate in enumerate(candidates)
    )
    return f"{rewritten_prefix}的{tail}"


def _split_value_question_prefix_and_tail(question: str) -> tuple[str | None, str | None]:
    normalized = normalize_question(question)
    if not normalized:
        return None, None

    for index in range(len(normalized) - 1, -1, -1):
        if normalized[index] != "的":
            continue
        tail = normalized[index + 1 :].strip()
        if tail and any(marker in tail for marker in VALUE_SUBSTITUTION_CONTEXT_MARKERS):
            prefix = normalized[:index].strip()
            return (prefix or None), tail

    positions = [
        normalized.find(marker)
        for marker in VALUE_TAIL_START_MARKERS
        if normalized.find(marker) > 0
    ]
    if not positions:
        return None, None
    tail_start = min(positions)
    prefix = normalized[:tail_start].rstrip("的").strip()
    tail = normalized[tail_start:].strip()
    return (prefix or None), (tail or None)


def _value_substitution_candidates(prefix: str) -> list[dict[str, str]]:
    normalized = normalize_question(prefix).strip()
    if not normalized:
        return []

    status_match = STATUS_VALUE_RE.search(normalized)
    if status_match and status_match.start() > 0:
        base = normalized[: status_match.start()].strip()
        status = status_match.group(0).strip()
        candidates: list[dict[str, str]] = []
        if base:
            candidates.append({"text": base, "kind": "generic"})
        if status:
            candidates.append({"text": status, "kind": "status"})
        return candidates
    return [{"text": normalized, "kind": "status" if _is_status_value(normalized) else "generic"}]


def _is_status_value(value: str) -> bool:
    normalized = _clean_dimension(value)
    return bool(normalized and STATUS_VALUE_RE.fullmatch(normalized))


def _rewrite_value_substitution_follow_up(
    question: str,
    history: list[dict[str, str]],
    rewrite_context: FollowUpRewriteContext,
) -> FollowUpRewrite | None:
    if not looks_like_follow_up(question):
        return None
    value = _extract_follow_up_value(question)
    if not value:
        return None

    anchor = _find_prior_question_anchor(
        history,
        question,
        rewrite_context,
        require_markers=VALUE_SUBSTITUTION_CONTEXT_MARKERS,
    )
    if not anchor:
        return None
    rewritten = _replace_substitution_value(
        anchor.effective_question,
        value,
        anchor.substitution_value,
    )
    if not rewritten:
        return None
    if rewritten in {question, anchor.effective_question}:
        return None
    return FollowUpRewrite(rewritten, "value_substitution_follow_up")


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


def _rewrite_chart_type_follow_up(
    question: str,
    history: list[dict[str, str]],
    rewrite_context: FollowUpRewriteContext,
) -> FollowUpRewrite | None:
    del rewrite_context
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
        FollowUpRule("value_substitution_follow_up", 30, _rewrite_value_substitution_follow_up),
        FollowUpRule("dimension_slot_follow_up", 10, _rewrite_dimension_slot_follow_up),
    ]
    return tuple(sorted(rules, key=lambda rule: -rule.priority))


_RULES = _build_rules()
