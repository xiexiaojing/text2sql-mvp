from __future__ import annotations

from text2sql_runtime.address_parser import build_room_name_path_likes, parse_room_address
from text2sql_runtime.address_slots import derive_address_like_slots


def test_parse_room_address_with_dash_separators():
    parsed = parse_room_address("马连道中里二区1号楼-1单元-203")
    assert parsed is not None
    assert parsed.community_area == "马连道中里二区"
    assert parsed.building == "1号楼"
    assert parsed.unit == "1单元"
    assert parsed.room == "203"


def test_build_room_name_path_likes_for_full_address():
    parsed = parse_room_address("马连道中里二区1号楼-1单元-203")
    assert parsed is not None
    likes = build_room_name_path_likes(parsed)
    assert "%1号楼%1单元%203%" in likes
    assert any("中里二区" in item for item in likes)


def test_derive_address_like_slots_uses_room_patterns():
    slots: dict[str, object] = {}
    derive_address_like_slots(slots, "马连道中里二区1号楼-1单元-203")
    assert slots["address_segment_like"] == "%1号楼%1单元%203%"
    assert slots["room_no"] == "203"
    assert slots["address_building_name"] == "1号楼"
    assert "203号楼" not in slots.values()
