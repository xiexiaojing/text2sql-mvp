from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER_PATTERN = r"[0-9]+(?:[0-9\-/\\ ]*[0-9]+)*"


@dataclass(frozen=True)
class ParsedRoomAddress:
    community_area: str
    building: str
    unit: str
    room: str
    normalized_address: str
    floor: str = ""


def parse_room_address(value: str) -> ParsedRoomAddress | None:
    text = _normalize_room_address_text(value)
    if not text:
        return None
    parsed = _parse_room_address_labeled(text)
    if parsed is not None:
        return parsed
    return _parse_room_address_delimited_tail(text)


def build_room_name_path_likes(parsed: ParsedRoomAddress) -> list[str]:
    paths: list[str] = []

    def add(*fragments: str) -> None:
        normalized = [fragment for fragment in fragments if fragment]
        if len(normalized) < 2:
            return
        pattern = "%" + "%".join(normalized) + "%"
        if pattern not in paths:
            paths.append(pattern)

    building = parsed.building
    unit = parsed.unit
    room = parsed.room
    add(building, unit, room)
    add(_numeric_label(building), _numeric_label(unit), room)
    for area in _community_area_variants(parsed.community_area):
        add(area, building, unit, room)
        add(area, _numeric_label(building), _numeric_label(unit), room)
    return paths


def _parse_room_address_labeled(value: str) -> ParsedRoomAddress | None:
    match = re.search(
        rf"(?P<building>{_NUMBER_PATTERN}(?:号楼|栋|幢|楼|号))\s*[-/\\ ]*"
        rf"(?:(?P<unit>{_NUMBER_PATTERN}(?:单元|单|元))\s*[-/\\ ]*)?"
        rf"(?:(?P<floor>{_NUMBER_PATTERN}(?:层|楼))\s*[-/\\ ]*)?"
        rf"(?P<room>{_NUMBER_PATTERN}[室房]?)$",
        value,
    )
    if match is None:
        return None
    community_area = value[: match.start()].rstrip("-/\\ ").strip()
    building = _normalize_building_label(match.group("building"))
    unit = _normalize_unit_label(match.group("unit")) if match.group("unit") else ""
    floor = _normalize_floor_label(match.group("floor")) if match.group("floor") else ""
    room = _normalize_room_label(match.group("room"))
    if not building or not room:
        return None
    return ParsedRoomAddress(
        community_area=community_area,
        building=building,
        unit=unit,
        room=room,
        normalized_address=f"{community_area}{building}{unit}{floor}{room}",
        floor=floor,
    )


def _parse_room_address_delimited_tail(value: str) -> ParsedRoomAddress | None:
    separator = r"\s*[-/\\ ]+\s*"
    match = re.search(
        rf"(?P<community_area>.*?)"
        rf"(?P<building>{_NUMBER_PATTERN}){separator}"
        rf"(?P<unit>{_NUMBER_PATTERN}){separator}"
        rf"(?:(?P<floor>{_NUMBER_PATTERN}){separator})?"
        rf"(?P<room>{_NUMBER_PATTERN}[室房]?)$",
        value,
    )
    if match is None:
        return None
    community_area = match.group("community_area").rstrip("-/\\ ").strip()
    building = _normalize_building_label(match.group("building"))
    unit = _normalize_unit_label(match.group("unit"))
    floor = _normalize_floor_label(match.group("floor")) if match.group("floor") else ""
    room = _normalize_room_label(match.group("room"))
    if not building or not room:
        return None
    return ParsedRoomAddress(
        community_area=community_area,
        building=building,
        unit=unit,
        room=room,
        normalized_address=f"{community_area}{building}{unit}{floor}{room}",
        floor=floor,
    )


def _normalize_room_address_text(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .replace("斜杠", "/")
        .replace("反斜杠", "/")
        .replace("／", "/")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("_", "-")
        .replace("杠", "-")
    )


def _normalize_building_label(value: str) -> str:
    return _normalize_labeled_number(value, suffixes=("号楼", "栋", "幢", "楼", "号"), output_suffix="号楼")


def _normalize_unit_label(value: str) -> str:
    return _normalize_labeled_number(value, suffixes=("单元", "单", "元"), output_suffix="单元")


def _normalize_floor_label(value: str) -> str:
    return _normalize_labeled_number(value, suffixes=("层", "楼"), output_suffix="层")


def _normalize_room_label(value: str) -> str:
    return _normalize_labeled_number(value, suffixes=("室", "房"), output_suffix="")


def _normalize_labeled_number(value: str, *, suffixes: tuple[str, ...], output_suffix: str) -> str:
    text = str(value or "").strip()
    for candidate in suffixes:
        if text.endswith(candidate):
            text = text[: -len(candidate)]
            break
    if re.fullmatch(r"\d+", text):
        return f"{text}{output_suffix}" if output_suffix else text
    return str(value or "").strip()


def _numeric_label(value: str) -> str:
    text = str(value or "").strip()
    for token in ("号楼", "栋", "幢", "单元", "单", "元", "层", "室", "房", "号", "楼"):
        text = text.replace(token, "")
    digits = re.findall(r"\d+", text)
    if digits:
        joined = "".join(digits).lstrip("0")
        return joined or "0"
    return text


def _community_area_variants(area: str) -> list[str]:
    cleaned = area.strip()
    if not cleaned:
        return []
    variants = [cleaned]
    if cleaned.startswith("马连道") and len(cleaned) > len("马连道"):
        variants.append(cleaned[len("马连道") :])
    return list(dict.fromkeys(item for item in variants if item))
