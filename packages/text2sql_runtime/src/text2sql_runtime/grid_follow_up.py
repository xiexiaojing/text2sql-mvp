from __future__ import annotations

import re

GRID_NAME_RE = re.compile(r"(第[一二三四五六七八九十0-9]+网格)")

GRID_SWITCH_FOLLOW_UP_RE = re.compile(
    r"^(?:那|也|再|就)?(第[一二三四五六七八九十0-9]+网格)(?:呢|的)?[？?。.!！]?$"
)

_GRID_NAME_PREFIX_BLOCKLIST = (
    "按",
    "各",
    "每",
    "本",
    "全",
    "该",
    "某",
    "这个",
    "哪个",
    "什么",
)

GRID_TOPIC_MARKERS = (
    "楼栋",
    "单元",
    "居民",
    "房间",
    "房屋",
    "包括哪几个楼",
    "有哪些楼",
    "包括哪些楼",
    "失能",
    "独居",
    "空巢",
    "老人",
    "占比",
    "诉求",
    "12345",
    "热线",
    "统一诉求",
    "党员",
    "台账",
    "有多少",
    "多少",
)

GRID_POPULATION_ASSISTANT_MARKERS = (
    "各网格人口",
    "各网格居民",
    "网格人口分布",
    "各网格人口合计",
    "各网格人口分布",
)


def extract_grid_name(question: str) -> str | None:
    preferred = GRID_NAME_RE.search(question)
    if preferred:
        return preferred.group(1)
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{1,12}网格)", question)
    if not match:
        return None
    name = match.group(1)
    if len(name) <= 3:
        return None
    if any(name.startswith(prefix) for prefix in _GRID_NAME_PREFIX_BLOCKLIST):
        return None
    return name


def is_grid_population_user_topic(question: str) -> bool:
    if "网格" not in question:
        return False
    if not re.search(r"居民|人口|人数", question):
        return False
    if re.search(r"诉求|工单|12345|热线|统一诉求|党员|楼栋|楼|热力图|失能|独居|空巢|台账|标签", question):
        return False
    return bool(re.search(r"统计|排名|分布|多少|合计|排行|对比", question))


def rewrite_grid_switch_follow_up(question: str, history: list[dict[str, str]]) -> str | None:
    normalized = _normalize_question(question).rstrip("？?。.!！")
    match = GRID_SWITCH_FOLLOW_UP_RE.match(normalized)
    if not match:
        return None
    prior = _find_prior_grid_topic_question(history, exclude=question)
    if not prior:
        return None
    rewritten = _replace_grid_name(prior, match.group(1))
    normalized_rewritten = _normalize_question(rewritten)
    if not normalized_rewritten or normalized_rewritten == _normalize_question(question):
        return None
    return rewritten


def is_grid_switch_follow_up(question: str) -> bool:
    normalized = _normalize_question(question).rstrip("？?。.!！")
    return bool(GRID_SWITCH_FOLLOW_UP_RE.match(normalized))


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip())


def _replace_grid_name(question: str, new_grid: str) -> str:
    cleaned = re.sub(r"^(?:我是说|我说的是)\s*", "", question.strip())
    match = GRID_NAME_RE.search(cleaned)
    if match:
        return cleaned[: match.start()] + new_grid + cleaned[match.end() :]
    if is_grid_population_user_topic(cleaned):
        return f"{new_grid}有多少居民"
    return f"{new_grid}{cleaned}"


def _find_prior_grid_topic_question(history: list[dict[str, str]], *, exclude: str) -> str | None:
    exclude_norm = _normalize_question(exclude)
    normalized_history = [
        {"role": item.get("role"), "content": _normalize_question(str(item.get("content", "")))}
        for item in history
        if item.get("role") in {"user", "assistant"} and str(item.get("content", "")).strip()
    ]
    for index in range(len(normalized_history) - 1, -1, -1):
        item = normalized_history[index]
        if item["role"] == "assistant":
            if not any(marker in item["content"] for marker in GRID_POPULATION_ASSISTANT_MARKERS):
                continue
            for prior in reversed(normalized_history[:index]):
                if prior["role"] != "user":
                    continue
                content = prior["content"]
                if not content or content == exclude_norm:
                    continue
                if is_grid_switch_follow_up(content):
                    continue
                if is_grid_population_user_topic(content):
                    return content
                if GRID_NAME_RE.search(content) and any(marker in content for marker in GRID_TOPIC_MARKERS):
                    return content
            return "按网格统计居民"
        if item["role"] != "user":
            continue
        content = item["content"]
        if not content or content == exclude_norm:
            continue
        if is_grid_switch_follow_up(content):
            continue
        if GRID_NAME_RE.search(content) and any(marker in content for marker in GRID_TOPIC_MARKERS):
            return content
    return None
