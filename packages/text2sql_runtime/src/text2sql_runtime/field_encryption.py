from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_ENCRYPTION_KEY = "小胖蟹"
DEFAULT_IV = "fix:code:iv:code"

CARD_ENCRYPTED_PARTIAL_LOOKUP_REASON = (
    "证件号已加密存储，无法按前缀或后缀查询，请提供完整身份证号。"
)

_STORED_CIPHERTEXT_PREFIXES = ("sm4:", "ss4:", "aes:")


@dataclass(frozen=True)
class FieldEncryptionSettings:
    enabled: bool = False
    key: str = DEFAULT_ENCRYPTION_KEY
    encryption_type: str = "sm4"

    @property
    def active(self) -> bool:
        return self.enabled and self.encryption_type not in {"", "none"}

    @classmethod
    def from_env(cls) -> FieldEncryptionSettings:
        enabled = _env_bool("TEXT2SQL_ENCRYPTION_ENABLED", False)
        key = os.getenv("TEXT2SQL_ENCRYPTION_KEY", DEFAULT_ENCRYPTION_KEY)
        encryption_type = os.getenv("TEXT2SQL_ENCRYPTION_TYPE", "sm4").strip().lower()
        return cls(enabled=enabled, key=key, encryption_type=encryption_type)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_key(key: str, encryption_type: str) -> bytes:
    effective_key = key
    if encryption_type == "ss4":
        effective_key = f"ss4{key}"
    return effective_key.encode("utf-8")[:16].ljust(16, b"\0")


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _load_sm4() -> tuple[Any, Any]:
    try:
        from gmssl.sm4 import SM4_ENCRYPT, CryptSM4
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Package 'gmssl' is required for SM4 field encryption. "
            "Install project dependencies: pip install -e ."
        ) from exc
    return SM4_ENCRYPT, CryptSM4


def is_stored_ciphertext(value: str) -> bool:
    lowered = value.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _STORED_CIPHERTEXT_PREFIXES)


def encrypt_field_value(plaintext: str, settings: FieldEncryptionSettings) -> str:
    if not settings.active:
        return plaintext
    if plaintext is None:
        return plaintext
    if is_stored_ciphertext(plaintext):
        return plaintext
    encryption_type = settings.encryption_type
    sm4_encrypt, crypt_sm4_cls = _load_sm4()
    if encryption_type == "sm4":
        key_bytes = _normalize_key(settings.key, "sm4")
        iv_bytes = DEFAULT_IV.encode("utf-8")
        crypt_sm4 = crypt_sm4_cls()
        crypt_sm4.set_key(key_bytes, sm4_encrypt)
        encrypted = crypt_sm4.crypt_cbc(iv_bytes, _pkcs7_pad(plaintext.encode("utf-8")))
        return "sm4:" + base64.b64encode(encrypted).decode("ascii")
    if encryption_type == "ss4":
        key_bytes = _normalize_key(settings.key, "ss4")
        iv_bytes = DEFAULT_IV.encode("utf-8")
        crypt_sm4 = crypt_sm4_cls()
        crypt_sm4.set_key(key_bytes, sm4_encrypt)
        encrypted = crypt_sm4.crypt_cbc(iv_bytes, _pkcs7_pad(plaintext.encode("utf-8")))
        return "ss4:" + base64.b64encode(encrypted).decode("ascii")
    raise ValueError(f"Unsupported encryption type: {encryption_type}")


def encrypt_sensitive_query_params(
    params: dict[str, Any],
    *,
    intent_id: str,
    settings: FieldEncryptionSettings,
) -> dict[str, Any]:
    if not settings.active:
        return params
    if intent_id == "resident_card_lookup":
        card_no = params.get("card_no")
        if not isinstance(card_no, str) or not card_no.strip():
            return params
        if is_stored_ciphertext(card_no):
            return params
        return {**params, "card_no": encrypt_field_value(card_no, settings)}
    if intent_id == "payment_phone_lookup":
        phone = params.get("phone")
        if not isinstance(phone, str) or not phone.strip():
            return params
        if is_stored_ciphertext(phone):
            return params
        return {**params, "phone": encrypt_field_value(phone, settings)}
    return params
