from text2sql_runtime.field_encryption import (
    DEFAULT_ENCRYPTION_KEY,
    FieldEncryptionSettings,
    decrypt_field_value,
    encrypt_field_value,
    encrypt_sensitive_query_params,
    is_stored_ciphertext,
)


def test_encrypt_field_value_matches_java_default_key():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    encrypted = encrypt_field_value("110101199001011234", settings)
    assert encrypted == "sm4:H7I5oeeo+X9JIqXxmtVTCrJmKznB9iov7C5daoo0ZDXjQvn+/Z+mB93v0zLrYFcl"


def test_decrypt_field_value_roundtrip_sm4():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    plaintext = "13800138000"
    encrypted = encrypt_field_value(plaintext, settings)
    assert decrypt_field_value(encrypted, settings) == plaintext


def test_is_stored_ciphertext():
    assert is_stored_ciphertext("sm4:abc")
    assert is_stored_ciphertext("SS4:abc")
    assert not is_stored_ciphertext("13800138000")


def test_encrypt_sensitive_query_params_encrypts_payment_phone():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    params = encrypt_sensitive_query_params(
        {"phone": "13800138000", "tenant_id": "demo"},
        intent_id="payment_phone_lookup",
        settings=settings,
    )
    assert params["phone"].startswith("sm4:")
    assert params["tenant_id"] == "demo"


def test_encrypt_sensitive_query_params_skips_other_intents():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    params = encrypt_sensitive_query_params(
        {"phone": "13800138000"},
        intent_id="payment_order_count",
        settings=settings,
    )
    assert params["phone"] == "13800138000"


def test_encrypt_field_value_supports_ss4_prefix():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="ss4")
    encrypted = encrypt_field_value("110101199001011234", settings)
    assert encrypted.startswith("ss4:")


def test_encrypt_field_value_ss4_matches_java_mix_encrypt_without_pkcs7_pad():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="ss4")
    encrypted = encrypt_field_value("370403199903080303", settings)
    assert encrypted == "ss4:+pdwl0SI+LQpo4c0+fWa1LIT9JkKptXX8kwHtK+EgNM="
