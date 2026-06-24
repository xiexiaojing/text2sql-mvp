from __future__ import annotations

import re

AFFIRMATIVE_REPLIES = frozenset({"是", "对", "是的", "嗯", "确认", "没错", "可以", "好的", "行"})

DEFAULT_CALIBER_CONFIRM_HINT = (
    "回复「是」或「对」即按默认口径查询；若不是，请直接说明或换一个完整问法。"
)

DEFAULT_CALIBER_MARKERS = (
    "我先按",
    "这样理解对吗",
    "这样理解对吗？",
    "对吗？",
    "如果您问的是两委一站人员",
)


def format_phrasing_alternatives(*questions: str) -> str:
    return " / ".join(f"「{question}」" for question in questions)


def format_caliber_example_line(*questions: str, label: str) -> str:
    return f"- {format_phrasing_alternatives(*questions)}——{label}"


def format_alternative_example_lines(lines: tuple[str, ...]) -> str:
    return "\n".join(lines)


def default_caliber_clarification_message(
    *,
    fuzzy_term: str,
    default_label: str,
    default_question: str,
    alternative_examples: tuple[str, ...],
) -> str:
    examples = "\n".join(f"- {item}" for item in alternative_examples)
    return (
        f"您问的「{fuzzy_term}」在本系统里可能对应多种统计口径。\n\n"
        f"我先按**{default_label}**来理解（对应问法：「{default_question}」）。这样理解对吗？\n"
        f"{DEFAULT_CALIBER_CONFIRM_HINT}\n\n"
        f"其他常见口径示例：\n{examples}"
    )


def is_default_caliber_clarification_message(content: str) -> bool:
    normalized = content.strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in DEFAULT_CALIBER_MARKERS)


def resolve_affirmative_reply(question: str) -> bool:
    original = question.strip().strip("？?。.!！,， ")
    if not original or len(original) > 6:
        return False
    if original in AFFIRMATIVE_REPLIES:
        return True
    normalized = original
    for prefix in ("就", "是", "要", "查", "看", "统计", "选", "按"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    for suffix in ("吧", "的", "人数", "数量", "总数", "口径"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized in AFFIRMATIVE_REPLIES


def extract_default_question_from_clarification(content: str) -> str | None:
    match = re.search(r"对应问法：「([^」]+)」", content)
    if match:
        return match.group(1).strip()
    if "如果您问的是两委一站人员" in content:
        return COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION
    return None


def rewrite_default_caliber_affirmative_follow_up(
    question: str,
    history: list[dict[str, str]],
) -> str | None:
    if not resolve_affirmative_reply(question):
        return None
    for item in reversed(history):
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role != "assistant" or not is_default_caliber_clarification_message(content):
            continue
        default_question = extract_default_question_from_clarification(content)
        if default_question:
            return default_question
    return None

PERSON_COUNT_DISAMBIGUATORS = (
    "居民",
    "常住人口",
    "户籍",
    "党员",
    "社工",
    "员工",
    "工作人员",
    "网格员",
    "志愿者",
    "老人",
    "老年人",
    "高龄",
    "商户",
    "店铺",
    "退役军人",
    "残疾人",
    "独居",
    "空巢",
    "失能",
    "两委",
    "书记",
    "主任",
    "副主任",
    "委员",
    "岗位",
    "迁出",
    "流动",
    "组织管理",
)

PERSON_COUNT_UMBRELLA_RE = re.compile(
    r"(有多少人|多少人|一共多少人|人数多少|有多少人口|人口总数|人口多少|人口规模|人口数)"
)

NON_PERSON_COUNT_RE = re.compile(
    r"(多少(个)?(楼|栋|幢|层|户|家|间|房|房间|网格|小区|院|商户|店|诉求|工单|走访|台账|案件|事件))"
)

COMMUNITY_PERSON_COUNT_RE = re.compile(r"社区.*(有多少人|多少人|有多少人口|人口总数|人口多少|人口规模|人口数)")

COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION = "组织管理在册人员有多少"
COMMUNITY_PERSON_COUNT_DEFAULT_PHRASINGS: tuple[str, ...] = (
    "组织管理在册人员有多少",
    "两委一站有多少人",
    "两委人员多少人",
    "社区组织人员总数",
    "组织管理人数多少",
)
PERSON_COUNT_DEFAULT_QUESTION = "本社区居民有多少"

COMMUNITY_PERSON_COUNT_ALTERNATIVE_EXAMPLES: tuple[str, ...] = (
    format_caliber_example_line(
        "本社区居民有多少",
        "社区人口有多少",
        "常住人口有多少",
        "本社区一共有多少居民",
        label="居民管理中在册人员",
    ),
    format_caliber_example_line(
        *COMMUNITY_PERSON_COUNT_DEFAULT_PHRASINGS,
        label="组织管理在册（两委一站）",
    ),
    format_caliber_example_line(
        "本社区党员有多少",
        "社区党员人数",
        "党员总数多少",
        "社区有多少党员",
        label="社区党员管理",
    ),
    format_caliber_example_line(
        "社区社工有多少人",
        "社区工作人员有多少",
        "社工人数多少",
        "员工人数多少",
        label="组织管理中社区工作人员（社工）",
    ),
    format_caliber_example_line(
        "社区网格员一共多少人",
        "网格员有多少",
        "网格成员人数",
        "本社区有多少网格员",
        label="网格管理成员",
    ),
    format_caliber_example_line(
        "社区志愿者有多少人",
        "志愿者总数多少",
        "注册志愿者人数",
        label="志愿者管理",
    ),
)


COMMUNITY_PERSON_COUNT_OTHER_CALIBERS: tuple[str, ...] = (
    "本社区居民有多少",
    "本社区党员有多少",
    "社区社工有多少人",
    "社区网格员有多少",
    "社区志愿者有多少人",
)


def format_community_person_count_default_answer(count: int) -> str:
    others = "、".join(COMMUNITY_PERSON_COUNT_OTHER_CALIBERS)
    return (
        f"如果您问的是两委一站人员，人数为{count}。\n\n"
        f"其他口径可问：{others}。"
    )


COMMUNITY_PERSON_COUNT_CLARIFICATION_MESSAGE = default_caliber_clarification_message(
    fuzzy_term="社区有多少人",
    default_label="两委一站（组织管理在册人员）",
    default_question=COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION,
    alternative_examples=COMMUNITY_PERSON_COUNT_ALTERNATIVE_EXAMPLES,
)

PERSON_COUNT_CLARIFICATION_MESSAGE = default_caliber_clarification_message(
    fuzzy_term="人/人数",
    default_label="居民管理中在册居民",
    default_question=PERSON_COUNT_DEFAULT_QUESTION,
    alternative_examples=(
        format_caliber_example_line(
            "本社区居民有多少",
            "社区人口有多少",
            "常住人口有多少",
            label="居民管理中在册人员",
        ),
        format_caliber_example_line(
            "组织管理在册人员有多少",
            "两委一站有多少人",
            "两委人员多少人",
            label="组织管理在册（两委一站）",
        ),
        format_caliber_example_line(
            "本社区党员有多少",
            "社区党员人数",
            "党员总数多少",
            label="社区党员管理",
        ),
        format_caliber_example_line(
            "社区社工有多少人",
            "社区工作人员有多少",
            "社工人数多少",
            label="组织管理中社区工作人员（社工）",
        ),
        format_caliber_example_line(
            "社区网格员一共多少人",
            "网格员有多少",
            "网格成员人数",
            label="网格管理成员",
        ),
        format_caliber_example_line(
            "社区志愿者有多少人",
            "志愿者总数多少",
            label="志愿者管理",
        ),
    ),
)

COMMUNITY_PERSON_COUNT_AFFIRMATIVE = AFFIRMATIVE_REPLIES

PERSON_COUNT_INTENT_IDS = frozenset(
    {
        "resident_count",
        "party_member_count",
        "employee_role_count",
        "grid_member_count",
        "employee_position_holder",
    }
)

PERSON_COUNT_CLARIFICATION_MARKERS = (
    "可能对应多种统计口径",
    "我先按",
    "这样理解对吗",
    "如果您问的是两委一站人员",
)

PERSON_COUNT_CHOICE_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("社区工作人员", "社区社工有多少人"),
    ("工作人员", "社区社工有多少人"),
    ("常住人口", "本社区居民有多少"),
    ("人口", "本社区居民有多少"),
    ("居民人口", "本社区居民有多少"),
    ("网格员", "社区网格员一共多少人"),
    ("网格成员", "社区网格员一共多少人"),
    ("志愿者", "社区志愿者有多少人"),
    ("居民", "本社区居民有多少"),
    ("党员", "本社区党员有多少"),
    ("社工", "社区社工有多少人"),
    ("员工", "社区社工有多少人"),
    ("组织管理", "组织管理在册人员有多少"),
    ("在册人员", "组织管理在册人员有多少"),
    ("组织人员", "组织管理在册人员有多少"),
    ("两委一站", "组织管理在册人员有多少"),
    ("两委", "组织管理在册人员有多少"),
    ("一站", "组织管理在册人员有多少"),
)

PERSON_COUNT_CHOICE_PREFIXES = ("就", "是", "要", "查", "看", "统计", "选")
PERSON_COUNT_CHOICE_SUFFIXES = ("吧", "的", "人数", "数量", "总数")

PERSON_COUNT_DIMENSION_PREFIXES = ("那", "也", "再", "就")
PERSON_COUNT_DIMENSION_SUFFIXES = ("呢", "吧", "的")

GENDER_COUNT_QUESTIONS: dict[tuple[str, str], str] = {
    ("resident", "男"): "男性居民有多少",
    ("resident", "女"): "女性居民有多少",
    ("party_member", "男"): "男性党员有多少",
    ("party_member", "女"): "女性党员有多少",
    ("employee", "男"): "组织管理在册男性有多少人",
    ("employee", "女"): "组织管理在册女性有多少人",
}

MARITAL_COUNT_QUESTIONS: dict[tuple[str, str], str] = {
    ("resident", "未婚"): "未婚居民有多少",
    ("resident", "已婚"): "已婚居民有多少",
    ("resident", "离异"): "离异居民有多少",
    ("resident", "丧偶"): "丧偶居民有多少",
}


def is_community_ambiguous_person_count(question: str) -> bool:
    normalized = question.strip()
    if not normalized or not COMMUNITY_PERSON_COUNT_RE.search(normalized):
        return False
    if NON_PERSON_COUNT_RE.search(normalized):
        return False
    return not any(term in normalized for term in PERSON_COUNT_DISAMBIGUATORS)


def person_count_clarification_reason(question: str) -> str | None:
    normalized = question.strip()
    if not normalized:
        return None
    if NON_PERSON_COUNT_RE.search(normalized):
        return None
    if not PERSON_COUNT_UMBRELLA_RE.search(normalized):
        return None
    if any(term in normalized for term in PERSON_COUNT_DISAMBIGUATORS):
        return None
    if is_community_ambiguous_person_count(normalized):
        return None
    return PERSON_COUNT_CLARIFICATION_MESSAGE


def missing_slot_clarification_reason(
    question: str,
    intent_id: str | None,
    missing_slots: list[str],
) -> str | None:
    if intent_id not in PERSON_COUNT_INTENT_IDS:
        return None
    if "role_like" not in missing_slots and "role" not in missing_slots:
        return None
    return person_count_clarification_reason(question)


def is_person_count_clarification_message(content: str) -> bool:
    normalized = content.strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in PERSON_COUNT_CLARIFICATION_MARKERS)


def is_community_person_count_clarification_message(content: str) -> bool:
    normalized = content.strip()
    if not normalized:
        return False
    if "如果您问的是两委一站人员" in normalized:
        return True
    return COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION in normalized


def is_community_person_count_clarification_context(history: list[dict[str, str]]) -> bool:
    for item in reversed(history):
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role == "assistant" and is_community_person_count_clarification_message(content):
            return True
        if role == "user" and is_community_ambiguous_person_count(content):
            return True
        if role == "user":
            break
    return False


def is_person_count_clarification_context(history: list[dict[str, str]]) -> bool:
    for item in reversed(history):
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role == "assistant" and (
            is_person_count_clarification_message(content)
            or is_community_person_count_clarification_message(content)
        ):
            return True
        if role == "user" and (
            person_count_clarification_reason(content)
            or is_community_ambiguous_person_count(content)
        ):
            return True
        if role == "user" and resolve_person_count_choice(content):
            continue
        if role == "user":
            break
    return False


def resolve_person_count_choice(question: str) -> str | None:
    normalized = _normalize_person_count_choice(question)
    if not normalized:
        return None
    for choice, full_question in sorted(PERSON_COUNT_CHOICE_QUESTIONS, key=lambda item: -len(item[0])):
        if normalized == choice:
            return full_question
    return None


def _normalize_person_count_choice(question: str) -> str:
    normalized = question.strip().strip("？?。.!！,， ")
    if not normalized or len(normalized) > 12:
        return ""
    for prefix in PERSON_COUNT_CHOICE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    for suffix in PERSON_COUNT_CHOICE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _normalize_person_count_dimension_follow_up(question: str) -> str:
    normalized = question.strip().strip("？?。.!！,， ")
    if not normalized or len(normalized) > 10:
        return ""
    for prefix in PERSON_COUNT_DIMENSION_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    changed = True
    while changed and normalized:
        changed = False
        for suffix in PERSON_COUNT_DIMENSION_SUFFIXES:
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)].strip()
                changed = True
                break
    return normalized


def caliber_from_resolved_person_count_question(question: str) -> str:
    if "党员" in question:
        return "party_member"
    if "网格员" in question:
        return "grid"
    if "志愿者" in question:
        return "volunteer"
    if any(term in question for term in ("社工", "工作人员", "组织管理", "两委", "一站")):
        return "employee"
    return "resident"


def infer_person_count_caliber(history: list[dict[str, str]]) -> str:
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        choice = resolve_person_count_choice(str(item.get("content", "")))
        if choice:
            return caliber_from_resolved_person_count_question(choice)
    for item in reversed(history):
        if item.get("role") != "assistant":
            continue
        content = str(item.get("content", ""))
        if "如果您问的是两委一站人员" in content:
            return "employee"
        if "可能对应多种统计口径" in content:
            return "resident"
    return "resident"


def infer_person_count_attribute_caliber(history: list[dict[str, str]]) -> str:
    for item in reversed(history):
        if item.get("role") != "user":
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        if _normalize_person_count_dimension_follow_up(content):
            continue
        choice = resolve_person_count_choice(content)
        if choice:
            return caliber_from_resolved_person_count_question(choice)
        if resolve_community_person_count_affirmative(content):
            return "employee"
        if resolve_affirmative_reply(content) and is_community_person_count_clarification_context(history):
            return "employee"
        if any(term in content for term in ("组织管理", "两委", "一站", "社工", "工作人员")) and re.search(
            r"有多少|多少人|人数|总数",
            content,
        ):
            return "employee"
        if is_community_ambiguous_person_count(content):
            return "resident"
        if person_count_clarification_reason(content):
            return "resident"
        break
    return "resident"


def resolve_person_count_attribute_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = _normalize_person_count_dimension_follow_up(question)
    if not normalized:
        return None
    caliber = infer_person_count_attribute_caliber(history)
    if normalized in {"男", "男性"}:
        return GENDER_COUNT_QUESTIONS.get((caliber, "男")) or GENDER_COUNT_QUESTIONS.get(("resident", "男"))
    if normalized in {"女", "女性"}:
        return GENDER_COUNT_QUESTIONS.get((caliber, "女")) or GENDER_COUNT_QUESTIONS.get(("resident", "女"))
    if caliber == "resident":
        marital_question = MARITAL_COUNT_QUESTIONS.get(("resident", normalized))
        if marital_question:
            return marital_question
    return None


def resolve_community_person_count_affirmative(question: str) -> str | None:
    original = question.strip().strip("？?。.!！,， ")
    if not original or len(original) > 6:
        return None
    if original in COMMUNITY_PERSON_COUNT_AFFIRMATIVE:
        return COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION
    normalized = original
    for prefix in PERSON_COUNT_CHOICE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    for suffix in PERSON_COUNT_CHOICE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    if normalized in COMMUNITY_PERSON_COUNT_AFFIRMATIVE:
        return COMMUNITY_PERSON_COUNT_EMPLOYEE_QUESTION
    return None


def rewrite_person_count_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    if not is_person_count_clarification_context(history):
        return None
    attribute_rewritten = resolve_person_count_attribute_follow_up(question, history)
    if attribute_rewritten:
        return attribute_rewritten
    if resolve_affirmative_reply(question):
        for item in reversed(history):
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content", "")).strip()
            if "老人" in content or "老年人" in content:
                continue
            default_question = extract_default_question_from_clarification(content)
            if default_question:
                return default_question
    return resolve_person_count_choice(question)


PROPERTY_COMPANY_CONTACT_DEFAULT_QUESTION = "物业公司经理电话是多少"

PROPERTY_COMPANY_CONTACT_CLARIFICATION_MESSAGE = default_caliber_clarification_message(
    fuzzy_term="物业公司负责人",
    default_label="经理岗位",
    default_question=PROPERTY_COMPANY_CONTACT_DEFAULT_QUESTION,
    alternative_examples=(
        format_caliber_example_line(
            "物业公司项目经理电话是多少",
            "物业项目经理联系方式",
            "物业项目负责人电话",
            label="项目经理岗位",
        ),
        format_caliber_example_line(
            "物业客服电话是多少",
            "物业客服联系方式",
            "物业前台电话",
            label="客服岗位",
        ),
        format_caliber_example_line(
            "物业维修人员电话是多少",
            "物业维修电话",
            "物业报修电话",
            label="维修岗位",
        ),
        format_caliber_example_line(
            "物业保安电话是多少",
            "物业门卫电话",
            label="保安岗位",
        ),
    ),
)

PROPERTY_COMPANY_RE = re.compile(r"物业(?:公司)?")
PROPERTY_RESPONSIBLE_PERSON_RE = re.compile(r"负责人|谁负责|负责的人|负责的是谁")
PROPERTY_CONTACT_ROLE_MARKERS = (
    "项目经理",
    "副经理",
    "经理",
    "客服",
    "联络员",
    "联系人",
    "维修",
    "保安",
    "电工",
    "会计",
    "财务",
    "主任",
    "副主任",
)

PROPERTY_CONTACT_ROLE_CHOICES: tuple[tuple[str, str], ...] = (
    ("项目经理", "物业公司项目经理电话是多少"),
    ("副经理", "物业公司副经理电话是多少"),
    ("经理", "物业公司经理电话是多少"),
    ("客服", "物业客服电话是多少"),
    ("维修", "物业维修人员电话是多少"),
    ("保安", "物业保安电话是多少"),
    ("电工", "物业电工电话是多少"),
    ("主任", "物业公司主任电话是多少"),
)

PROPERTY_CONTACT_CHOICE_PREFIXES = ("就", "是", "要", "查", "看", "找")
PROPERTY_CONTACT_CHOICE_SUFFIXES = ("吧", "的", "电话", "联系方式", "手机", "号码")

PROPERTY_CONTACT_CLARIFICATION_MARKERS = (
    "可能对应多种统计口径",
    "我先按",
    "这样理解对吗",
    "没有统一的「负责人」岗位称谓",
)


def property_company_contact_clarification_reason(question: str) -> str | None:
    normalized = question.strip()
    if not normalized or not PROPERTY_COMPANY_RE.search(normalized):
        return None
    if "网格" in normalized or "商户" in normalized or "店铺" in normalized:
        return None
    if not PROPERTY_RESPONSIBLE_PERSON_RE.search(normalized):
        return None
    if re.search(r"(电话|联系方式|手机号|联系电话)", normalized):
        return None
    if any(marker in normalized for marker in PROPERTY_CONTACT_ROLE_MARKERS):
        return None
    return PROPERTY_COMPANY_CONTACT_CLARIFICATION_MESSAGE


def is_property_company_contact_clarification_message(content: str) -> bool:
    normalized = content.strip()
    if not normalized:
        return False
    return any(marker in normalized for marker in PROPERTY_CONTACT_CLARIFICATION_MARKERS)


def is_property_company_contact_clarification_context(history: list[dict[str, str]]) -> bool:
    for item in reversed(history):
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role == "assistant" and is_property_company_contact_clarification_message(content):
            return True
        if role == "user" and property_company_contact_clarification_reason(content):
            return True
        if role == "user":
            break
    return False


def resolve_property_contact_role_choice(question: str) -> str | None:
    normalized = question.strip().strip("？?。.!！,， ")
    if not normalized or len(normalized) > 12:
        return None
    for prefix in PROPERTY_CONTACT_CHOICE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
    for suffix in PROPERTY_CONTACT_CHOICE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    if not normalized:
        return None
    for choice, full_question in sorted(PROPERTY_CONTACT_ROLE_CHOICES, key=lambda item: -len(item[0])):
        if normalized == choice:
            return full_question
    return None


def rewrite_property_company_contact_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    if not is_property_company_contact_clarification_context(history):
        return None
    if resolve_affirmative_reply(question):
        for item in reversed(history):
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content", "")).strip()
            default_question = extract_default_question_from_clarification(content)
            if default_question:
                return default_question
    return resolve_property_contact_role_choice(question)


ELDERLY_AMBIGUOUS_RE = re.compile(r"(老人|老年人)")
ELDERLY_DEFAULT_QUESTION = "本社区老年人有多少"
ELDERLY_SPECIFIC_MARKERS = (
    ELDERLY_DEFAULT_QUESTION,
    "社区有多少老人",
    "失能老人",
    "独居老人",
    "独居老年人",
    "空巢老人",
    "高龄老人",
    "60岁",
    "60 岁",
    "80岁",
    "80 岁",
    "占比",
    "比例",
    "党员",
    "标签",
)

ELDERLY_TAG_DEFAULTS: tuple[tuple[str, str, str], ...] = (
    ("失能", "失能老人（标签人群）", "本社区失能老人有多少"),
    ("独居", "独居老人（标签人群）", "本社区独居老人有多少"),
    ("空巢", "空巢老人（标签人群）", "本社区空巢老人有多少"),
)

ELDERLY_AGE_TOPIC_MARKERS = ("各年龄段", "年龄段", "年龄分布", "年龄结构")


def infer_elderly_default_from_history(history: list[dict[str, str]] | None) -> tuple[str, str]:
    grid_name: str | None = None
    tag_marker: str | None = None
    age_topic = False
    if history:
        for item in reversed(history):
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            grid_match = re.search(r"(第[一二三四五六七八九十0-9]+网格)", content)
            if grid_match:
                grid_name = grid_match.group(1)
            for marker, _label, _question in ELDERLY_TAG_DEFAULTS:
                if marker in content:
                    tag_marker = marker
            if any(marker in content for marker in ELDERLY_AGE_TOPIC_MARKERS):
                age_topic = True

    if grid_name and tag_marker == "失能":
        return "失能老人（标签人群）", f"{grid_name}有几户失能老人"
    if grid_name and tag_marker == "独居":
        return "独居老人（标签人群）", f"{grid_name}有几户独居老人"
    if grid_name and tag_marker == "空巢":
        return "空巢老人（标签人群）", f"{grid_name}有几户空巢老人"
    if tag_marker:
        for marker, label, question in ELDERLY_TAG_DEFAULTS:
            if marker == tag_marker:
                return label, question
    if age_topic:
        return "60岁及以上居民（按出生日期）", ELDERLY_DEFAULT_QUESTION
    return "60岁及以上居民（按出生日期）", ELDERLY_DEFAULT_QUESTION


def elderly_clarification_reason(
    question: str,
    history: list[dict[str, object]] | None = None,
) -> str | None:
    normalized = question.strip()
    if not normalized or not ELDERLY_AMBIGUOUS_RE.search(normalized):
        return None
    if any(marker in normalized for marker in ELDERLY_SPECIFIC_MARKERS):
        return None
    if "网格" in normalized and any(marker in normalized for marker in ("几户", "户数", "多少户")):
        return None
    if not re.search(r"(有多少|多少|几人|几户|人数|总数|规模)", normalized):
        return None

    normalized_history = [
        {"role": str(item.get("role", "")), "content": str(item.get("content", ""))}
        for item in (history or [])
        if str(item.get("role", "")) in {"user", "assistant"} and str(item.get("content", "")).strip()
    ]
    default_label, default_question = infer_elderly_default_from_history(normalized_history)
    fuzzy_term = "老人/老年人"
    if any(marker in normalized for marker in ELDERLY_AGE_TOPIC_MARKERS):
        fuzzy_term = "老人（在聊年龄结构后的追问）"
    return default_caliber_clarification_message(
        fuzzy_term=fuzzy_term,
        default_label=default_label,
        default_question=default_question,
        alternative_examples=(
            format_caliber_example_line(
                "本社区老年人有多少",
                "60岁以上老人有多少",
                "60岁及以上居民有多少",
                label="60岁及以上居民（按出生日期）",
            ),
            format_caliber_example_line(
                "80岁以上高龄老人有多少",
                "高龄老人有多少",
                "80岁及以上老人有多少",
                label="80岁及以上高龄老人",
            ),
            format_caliber_example_line(
                "本社区失能老人有多少",
                "第一网格有几户失能老人",
                label="失能老人（标签人群，可带网格）",
            ),
            format_caliber_example_line(
                "本社区独居老人有多少",
                "第一网格有几户独居老人",
                label="独居老人（标签人群，可带网格）",
            ),
            format_caliber_example_line(
                "第一网格60岁以上老人占比",
                "网格老年人占比",
                label="网格内老年人口占比",
            ),
        ),
    )


def is_elderly_clarification_context(history: list[dict[str, str]]) -> bool:
    for item in reversed(history):
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role == "assistant" and is_default_caliber_clarification_message(content):
            if "老人" in content or "老年人" in content:
                return True
        if role == "user" and elderly_clarification_reason(content):
            return True
        if role == "user":
            break
    return False


def rewrite_elderly_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    if not is_elderly_clarification_context(history):
        return None
    if resolve_affirmative_reply(question):
        for item in reversed(history):
            if item.get("role") != "assistant":
                continue
            content = str(item.get("content", "")).strip()
            if "老人" not in content and "老年人" not in content:
                continue
            default_question = extract_default_question_from_clarification(content)
            if default_question:
                return default_question
    normalized = question.strip().strip("？?。.!！,， ")
    if normalized in {"60岁", "60岁以上", "六十岁"}:
        return ELDERLY_DEFAULT_QUESTION
    for marker, _label, default_question in ELDERLY_TAG_DEFAULTS:
        if marker in normalized:
            return default_question
    return None
