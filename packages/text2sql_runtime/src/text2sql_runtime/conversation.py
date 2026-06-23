from __future__ import annotations

import re
from typing import Any

from .visualization import CHART_TYPE_KEYWORDS, CHART_TYPE_LABELS, detect_requested_chart_type

DOMAIN_SUBJECTS = [
    ("payment_order", "支付订单", ["支付订单", "订单", "交易订单"]),
    ("refund_order", "退款", ["退款", "退款订单", "退单"]),
    ("merchant", "商户", ["商户", "商家", "门店"]),
]

FOLLOW_UP_MARKERS = [
    "那",
    "这个",
    "那个",
    "刚才",
    "上面",
    "继续",
    "再",
    "也",
    "呢",
    "换成",
    "改成",
]

# 已是完整问句时，不应因会话历史被改写成泛化统计口径。
STANDALONE_QUERY_MARKERS = [
    "包括哪几个",
    "包括哪些",
    "有哪些",
    "有多少",
    "多少",
    "有几户",
    "几户",
    "几个楼",
    "哪些楼",
    "楼栋",
    "TOP",
    "top",
    "是谁",
    "归谁",
    "归哪个",
    "姓名",
    "手机",
    "名单",
    "列表",
    "明细",
    "详情",
    "占比",
    "比例",
    "排名",
]

GROUP_DIMENSIONS = ["状态", "渠道", "来源", "分类", "类别", "商户", "金额"]


def normalize_history(history: list[dict[str, Any]] | None, *, max_turns: int = 8) -> list[dict[str, str]]:
    if not history:
        return []
    normalized: list[dict[str, str]] = []
    for item in history[-max_turns:]:
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": _compact(content)})
    return normalized


def contextualize_question(question: str, history: list[dict[str, Any]] | None) -> tuple[str, dict[str, Any] | None]:
    current = _normalize_question(question)
    normalized_history = normalize_history(history)
    if not normalized_history:
        return current, None

    chart_rewritten = _rewrite_chart_type_follow_up(current, normalized_history)
    if chart_rewritten and chart_rewritten != current:
        return chart_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": chart_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "chart_type_follow_up",
        }

    from .disambiguation import rewrite_person_count_follow_up

    person_count_rewritten = rewrite_person_count_follow_up(current, normalized_history)
    if person_count_rewritten and person_count_rewritten != current:
        return person_count_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": person_count_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "person_count_choice_follow_up",
        }

    from .disambiguation import rewrite_elderly_follow_up

    elderly_rewritten = rewrite_elderly_follow_up(current, normalized_history)
    if elderly_rewritten and elderly_rewritten != current:
        return elderly_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": elderly_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "elderly_default_caliber_follow_up",
        }

    from .disambiguation import rewrite_property_company_contact_follow_up

    property_contact_rewritten = rewrite_property_company_contact_follow_up(current, normalized_history)
    if property_contact_rewritten and property_contact_rewritten != current:
        return property_contact_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": property_contact_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "property_company_contact_role_follow_up",
        }

    from .phone_follow_up import rewrite_phone_detail_follow_up

    phone_detail_rewritten = rewrite_phone_detail_follow_up(current, normalized_history)
    if phone_detail_rewritten and phone_detail_rewritten != current:
        return phone_detail_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": phone_detail_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "phone_detail_follow_up",
        }

    from .list_follow_up import rewrite_list_detail_follow_up

    list_detail_rewritten = rewrite_list_detail_follow_up(current, normalized_history)
    if list_detail_rewritten and list_detail_rewritten != current:
        return list_detail_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": list_detail_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "list_detail_follow_up",
        }

    from .ledger_follow_up import rewrite_ledger_updater_follow_up

    ledger_updater_rewritten = rewrite_ledger_updater_follow_up(current, normalized_history)
    if ledger_updater_rewritten and ledger_updater_rewritten != current:
        return ledger_updater_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": ledger_updater_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "ledger_updater_follow_up",
        }

    from .grid_follow_up import is_grid_switch_follow_up, rewrite_grid_switch_follow_up

    grid_switch_rewritten = rewrite_grid_switch_follow_up(current, normalized_history)
    if grid_switch_rewritten and grid_switch_rewritten != current:
        return grid_switch_rewritten, {
            "kind": "conversation",
            "status": "ok",
            "originalQuestion": question,
            "effectiveQuestion": grid_switch_rewritten,
            "history": normalized_history[-4:],
            "rewriteReason": "grid_switch_follow_up",
        }

    if is_grid_switch_follow_up(current):
        return current, None

    previous_user = _last_user_message(normalized_history)
    if not previous_user:
        return current, None
    if not _looks_like_follow_up(current):
        return current, None
    if _contains_domain_subject(current):
        rewritten = _rewrite_follow_up(current)
    else:
        subject = _subject_from_history(normalized_history)
        if not subject:
            return current, None
        rewritten = f"{subject} {_rewrite_follow_up(current)}".strip()

    if rewritten == current:
        return current, None
    return rewritten, {
        "kind": "conversation",
        "status": "ok",
        "originalQuestion": question,
        "effectiveQuestion": rewritten,
        "history": normalized_history[-4:],
    }


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


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _compact(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip())[:600]


def _last_user_message(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item["role"] == "user":
            return item["content"]
    return None


def _looks_like_follow_up(question: str) -> bool:
    if any(marker in question for marker in STANDALONE_QUERY_MARKERS):
        return False
    if any(question.startswith(prefix) for prefix in ["那", "这个", "那个", "刚才", "上面", "继续", "再", "也"]):
        return True
    if "换成" in question or "改成" in question:
        return True
    if question.endswith("呢") and len(question) <= 10:
        return True
    return any(marker in question for marker in FOLLOW_UP_MARKERS)


def _contains_domain_subject(question: str) -> bool:
    return any(keyword in question for _, _, keywords in DOMAIN_SUBJECTS for keyword in keywords)


def _subject_from_history(history: list[dict[str, str]]) -> str | None:
    for item in reversed(history):
        if item["role"] != "user":
            continue
        content = item["content"]
        for _, subject, keywords in DOMAIN_SUBJECTS:
            if any(keyword in content for keyword in keywords):
                return subject
    return None


def _previous_user_question(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item["role"] != "user":
            continue
        content = _normalize_question(item["content"])
        if content and content != exclude_norm:
            return item["content"]
    return None


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


_TOPIC_PATTERN = re.compile(
    r"([\u4e00-\u9fff\dA-Za-z]+(?:渠道|状态|金额|退款|商户)?(?:分布|统计|排名|占比|结构|构成|情况|趋势))"
)


def _extract_chart_topic(text: str) -> str | None:
    normalized = _normalize_question(text)
    if not normalized:
        return None
    chart_pattern = "|".join(
        re.escape(keyword)
        for keyword in sorted(_all_chart_keywords(), key=len, reverse=True)
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
        re.escape(label)
        for label in sorted(set(CHART_TYPE_LABELS.values()), key=len, reverse=True)
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
    exclude_norm = _normalize_question(exclude)
    for item in reversed(history):
        if item["role"] != "user":
            continue
        content = _normalize_question(item["content"])
        if not content or content == exclude_norm:
            continue
        topic = _extract_chart_topic(item["content"])
        if topic:
            return topic
    for item in reversed(history):
        if item["role"] != "assistant":
            continue
        topic = _extract_chart_topic_from_assistant(item["content"])
        if topic:
            return topic
    return None


def _find_prior_chart_question(history: list[dict[str, str]], exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    last_qualified: str | None = None
    for item in history:
        if item["role"] != "user":
            continue
        content = _normalize_question(item["content"])
        if not content or content == exclude_norm:
            continue
        if (
            _extract_chart_topic(item["content"])
            or (
                detect_requested_chart_type(item["content"])
                and (
                    _contains_domain_subject(item["content"])
                    or any(marker in item["content"] for marker in ["分布", "统计", "排名", "占比", "趋势"])
                )
            )
        ):
            last_qualified = item["content"]
    if last_qualified and _extract_chart_topic(last_qualified):
        return last_qualified

    return _previous_user_question(history, exclude)


def _infer_chart_question_from_assistant(content: str) -> str | None:
    topic = _extract_chart_topic_from_assistant(content)
    if not topic:
        return None
    return f"生成一份{topic}饼图"


def _rewrite_chart_type_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    requested = detect_requested_chart_type(question)
    if requested is None:
        return None
    if not _looks_like_follow_up(question) and not _is_bare_chart_type_follow_up(question):
        return None

    new_label = _preferred_chart_label(question, requested)
    if not new_label:
        return None

    topic = _find_prior_chart_topic(history, exclude=question)
    if topic:
        return f"生成一份{topic}{new_label}"

    previous = _find_prior_chart_question(history, exclude=question)
    if not previous:
        return None

    previous_chart = detect_requested_chart_type(previous)
    if previous_chart is not None:
        for keyword in sorted(_chart_keywords(previous_chart), key=len, reverse=True):
            if keyword in previous:
                return previous.replace(keyword, new_label, 1)

    merged = _strip_chart_keywords(previous)
    merged = re.sub(r"\s+", " ", merged).strip(" ，,、")
    if new_label not in merged:
        merged = f"{merged}{new_label}"
    return merged.strip()


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


def _rewrite_follow_up(question: str) -> str:
    if any(marker in question for marker in STANDALONE_QUERY_MARKERS):
        rewritten = question
        for prefix in ["那", "这个", "那个", "刚才", "上面", "继续", "再", "也"]:
            rewritten = re.sub(rf"^{prefix}", "", rewritten).strip()
        return rewritten.rstrip("？?。.!！").rstrip("呢").strip()

    rewritten = question
    for prefix in ["那", "这个", "那个", "刚才", "上面", "继续", "再", "也"]:
        rewritten = re.sub(rf"^{prefix}", "", rewritten).strip()
    rewritten = rewritten.rstrip("？?。.!！")
    rewritten = rewritten.rstrip("呢").strip()
    if "换成" in rewritten:
        rewritten = rewritten.replace("换成", "按")
    if "改成" in rewritten:
        rewritten = rewritten.replace("改成", "按")
    if not any(marker in rewritten for marker in ["按", "各", "分组", "排名", "分布"]):
        for dimension in GROUP_DIMENSIONS:
            if dimension in rewritten:
                rewritten = f"按{dimension}统计"
                break
    elif rewritten.startswith("按") and not any(
        suffix in rewritten for suffix in ["统计", "分组", "排名", "数量"]
    ):
        rewritten = f"{rewritten}统计"
    return rewritten

