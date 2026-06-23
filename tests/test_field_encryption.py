from text2sql_runtime.field_encryption import (
    DEFAULT_ENCRYPTION_KEY,
    FieldEncryptionSettings,
    encrypt_field_value,
    encrypt_sensitive_query_params,
    is_stored_ciphertext,
)


def test_encrypt_field_value_matches_java_default_key():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    encrypted = encrypt_field_value("110101199001011234", settings)
    assert encrypted == "sm4:H7I5oeeo+X9JIqXxmtVTCrJmKznB9iov7C5daoo0ZDXjQvn+/Z+mB93v0zLrYFcl"


def test_is_stored_ciphertext():
    assert is_stored_ciphertext("sm4:abc")
    assert is_stored_ciphertext("SS4:abc")
    assert not is_stored_ciphertext("110101199001011234")


def test_encrypt_sensitive_query_params_only_encrypts_exact_card_no():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    params = encrypt_sensitive_query_params(
        {"card_no": "110101199001011234", "card_prefix_like": "370403%"},
        intent_id="resident_card_lookup",
        settings=settings,
    )
    assert params["card_no"].startswith("sm4:")
    assert params["card_prefix_like"] == "370403%"


def test_encrypt_sensitive_query_params_skips_other_intents():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    params = encrypt_sensitive_query_params(
        {"card_no": "110101199001011234"},
        intent_id="payment_order_count",
        settings=settings,
    )
    assert params["card_no"] == "110101199001011234"


def test_encrypt_sensitive_query_params_encrypts_payment_phone():
    settings = FieldEncryptionSettings(enabled=True, key=DEFAULT_ENCRYPTION_KEY, encryption_type="sm4")
    params = encrypt_sensitive_query_params(
        {"phone": "13800138000"},
        intent_id="payment_phone_lookup",
        settings=settings,
    )
    assert params["phone"].startswith("sm4:")
