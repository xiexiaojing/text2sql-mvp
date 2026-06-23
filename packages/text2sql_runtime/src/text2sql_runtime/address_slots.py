from __future__ import annotations

import re
from typing import Any

from .address_parser import build_room_name_path_likes, parse_room_address


def derive_address_like_slots(slots: dict[str, Any], raw_value: str) -> None:
    address = _normalize_compound_building_address(_clean_slot_text(raw_value.strip().strip("%")).rstrip("下里内"))
    if not address:
        return
    parsed = parse_room_address(address)
    if parsed is not None:
        likes = build_room_name_path_likes(parsed)
        if likes:
            slots["address_like"] = _like_value(address)
            slots["address_segment_like"] = likes[0]
            if len(likes) > 1:
                slots["address_alias_segment_like"] = likes[1]
            if len(likes) > 2:
                slots["address_second_alias_segment_like"] = likes[2]
            if len(likes) > 3:
                slots["address_broad_like"] = likes[3]
            for area in _community_area_variants(parsed.community_area):
                slots.setdefault("address_area_like", _like_value(area))
            slots["room_no"] = parsed.room
            if parsed.building:
                slots["address_building_name"] = parsed.building
            return
    patterns = _address_segment_like_patterns(address)
    building_names, area_prefix = _address_building_parts(address)
    slots["address_like"] = _like_value(address)
    slots["address_segment_like"] = patterns[0]
    if len(patterns) > 1:
        slots["address_alias_segment_like"] = patterns[1]
    if len(patterns) > 2:
        slots["address_second_alias_segment_like"] = patterns[2]
    if building_names:
        slots["address_building_name"] = building_names[0]
    if len(building_names) > 1:
        slots["address_building_alias_name"] = building_names[1]
    if len(building_names) > 2:
        slots["address_building_second_alias_name"] = building_names[2]
    if area_prefix:
        slots["address_area_like"] = _like_value(area_prefix)
    if _safe_for_broad_address_like(address):
        slots["address_broad_like"] = _like_value(address)


def _clean_slot_text(value: str) -> str:
    cleaned = value.strip(" \t\r\n，。？！?的“”‘’\"'")
    for prefix in ("请问", "帮我查", "查询", "查一下"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned.strip(" \t\r\n，。？！?的“”‘’\"'")


def _like_value(value: str) -> str:
    return f"%{value}%"


def _normalize_compound_building_address(address: str) -> str:
    cleaned = _clean_slot_text(address)
    if not cleaned:
        return cleaned
    match = re.fullmatch(r"(.+?)([0-9]+)号(?!(?:楼|栋|幢))", cleaned)
    if match:
        return f"{match.group(1)}{match.group(2)}号楼"
    return cleaned


def _community_area_variants(area: str) -> list[str]:
    cleaned = area.strip()
    if not cleaned:
        return []
    return [cleaned]


def _address_building_parts(address: str) -> tuple[list[str], str | None]:
    if parse_room_address(address) is not None:
        return [], None
    cleaned = _clean_slot_text(address)
    if match := re.fullmatch(r"([0-9]+(?:号)?(?:楼|栋)?)", cleaned):
        return _building_name_aliases(match.group(1)), None
    if match := re.fullmatch(r"(.+?)([0-9]+(?:号)?(?:楼|栋)?)", cleaned):
        return _building_name_aliases(match.group(2)), _normalize_area_prefix(match.group(1))
    return [], None


def _building_name_aliases(building_text: str) -> list[str]:
    match = re.match(r"([0-9]+)", building_text)
    if not match:
        return [building_text] if building_text else []
    number = match.group(1)
    canonical = f"{number}号楼"
    aliases = [building_text, canonical, number, f"{number}栋"]
    if building_text.endswith("号") and not building_text.endswith(("号楼", "栋", "幢")):
        aliases = [canonical, building_text, number, f"{number}栋"]
    return list(dict.fromkeys(aliases))


def _normalize_area_prefix(prefix: str) -> str | None:
    cleaned = _clean_slot_text(prefix).strip("/")
    for suffix in ("社区", "小区", "园区"):
        if cleaned.endswith(suffix) and len(cleaned) > len(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    return cleaned or None


def _address_segment_like_patterns(address: str) -> list[str]:
    cleaned = _clean_slot_text(address)
    patterns = [_path_segment_like(cleaned)]
    if match := re.fullmatch(r"([0-9]+)(?:号)?(?:楼|栋)?", cleaned):
        number = match.group(1)
        patterns.extend(_path_segment_like(value) for value in [number, f"{number}号楼", f"{number}栋"])
    elif match := re.search(r"(.+?)([0-9]+)(?:号)?(?:楼|栋)?$", cleaned):
        prefix = match.group(1)
        number = match.group(2)
        patterns.extend(
            [
                _contextual_path_segment_like(prefix, number),
                _contextual_path_segment_like(prefix, f"{number}号楼"),
                _contextual_path_segment_like(prefix, f"{number}栋"),
            ]
        )
    return list(dict.fromkeys(item for item in patterns if item))


def _path_segment_like(value: str) -> str:
    return f"%/{value}/%"


def _contextual_path_segment_like(prefix: str, value: str) -> str:
    return f"%/{prefix}%/{value}/%"


def _safe_for_broad_address_like(address: str) -> bool:
    return not re.fullmatch(r"[0-9]+(?:号)?(?:楼|栋)?", address) and len(address) >= 4
