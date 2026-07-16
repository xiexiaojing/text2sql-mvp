from __future__ import annotations

from text2sql_runtime.field_encryption import FieldEncryptionSettings, encrypt_field_value
from text2sql_runtime.formatter import _format_row_display_values


def test_numeric_merchant_name_is_not_formatted_as_timestamp():
    row = {"merchant_name": "22222222222222222", "total": 1000}

    formatted = _format_row_display_values(row)

    assert formatted["merchant_name"] == "22222222222222222"


def test_timestamp_columns_still_format_epoch_values():
    row = {"created_at": 1760148894935}

    formatted = _format_row_display_values(row)

    assert formatted["created_at"].startswith("2025/10/11")


def test_stored_ciphertext_columns_are_decrypted_for_display():
    settings = FieldEncryptionSettings(enabled=True, key="阿弥陀佛", encryption_type="sm4")
    encrypted = encrypt_field_value("13800138000", settings)
    row = {"payer_mobile": encrypted}

    formatted = _format_row_display_values(row, settings)

    assert formatted["payer_mobile"] == "13800138000"
