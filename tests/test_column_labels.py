from __future__ import annotations

from text2sql_runtime.column_labels import resolve_column_display_labels


def test_channel_value_maps_to_chinese_label():
    labels = resolve_column_display_labels(["channel_value", "total"], None)
    assert labels == ["渠道", "数量"]


def test_merchant_name_maps_to_chinese_label():
    labels = resolve_column_display_labels(["merchant_name", "total"], None)
    assert labels == ["商户名称", "数量"]
