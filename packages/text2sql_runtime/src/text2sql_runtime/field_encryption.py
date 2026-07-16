from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

DEFAULT_ENCRYPTION_KEY = "阿弥陀佛"
DEFAULT_IV = "fix:code:iv:code"

_STORED_CIPHERTEXT_PREFIXES = ("sm4:", "ss4:", "aes:")

_SENSITIVE_QUERY_INTENTS: frozenset[str] = frozenset({"payment_phone_lookup"})


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


def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
    if not data:
        return data
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        return data
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        return data
    return data[:-pad_len]


def _load_sm4() -> tuple[Any, Any, Any]:
    try:
        from gmssl.sm4 import SM4_DECRYPT, SM4_ENCRYPT, CryptSM4
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "Package 'gmssl' is required for SM4 field encryption. "
            "Install project dependencies: pip install -e ."
        ) from exc
    return SM4_ENCRYPT, SM4_DECRYPT, CryptSM4


def is_stored_ciphertext(value: str) -> bool:
    lowered = value.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _STORED_CIPHERTEXT_PREFIXES)


def _decrypt_cbc(ciphertext: str, *, key: str) -> str:
    if not is_stored_ciphertext(ciphertext):
        return ciphertext
    encryption_type, _, encoded = ciphertext.partition(":")
    encryption_type = encryption_type.strip().lower()
    if encryption_type not in {"sm4", "ss4"}:
        return ciphertext
    _, sm4_decrypt, crypt_sm4_cls = _load_sm4()
    key_bytes = _normalize_key(key, encryption_type)
    iv_bytes = DEFAULT_IV.encode("utf-8")
    encrypted = base64.b64decode(encoded)
    crypt_sm4 = crypt_sm4_cls()
    crypt_sm4.set_key(key_bytes, sm4_decrypt)
    decrypted = crypt_sm4.crypt_cbc(iv_bytes, encrypted)
    if encryption_type == "sm4":
        decrypted = _pkcs7_unpad(decrypted)
    else:
        decrypted = decrypted.rstrip(b"\0")
    return decrypted.decode("utf-8")


def decrypt_field_value(ciphertext: str, settings: FieldEncryptionSettings) -> str:
    if not settings.active or ciphertext is None:
        return str(ciphertext) if ciphertext is not None else ciphertext
    text = str(ciphertext)
    if not is_stored_ciphertext(text):
        return text
    return _decrypt_cbc(text, key=settings.key)


def _encrypt_cbc(plaintext: str, *, encryption_type: str, key: str) -> str:
    sm4_encrypt, _, crypt_sm4_cls = _load_sm4()
    key_bytes = _normalize_key(key, encryption_type)
    iv_bytes = DEFAULT_IV.encode("utf-8")
    payload = plaintext.encode("utf-8")
    if encryption_type == "sm4":
        payload = _pkcs7_pad(payload)
    crypt_sm4 = crypt_sm4_cls()
    crypt_sm4.set_key(key_bytes, sm4_encrypt)
    encrypted = crypt_sm4.crypt_cbc(iv_bytes, payload)
    return f"{encryption_type}:" + base64.b64encode(encrypted).decode("ascii")


def encrypt_field_value(plaintext: str, settings: FieldEncryptionSettings) -> str:
    if not settings.active:
        return plaintext
    if plaintext is None:
        return plaintext
    if is_stored_ciphertext(plaintext):
        return plaintext
    encryption_type = settings.encryption_type
    if encryption_type in {"sm4", "ss4"}:
        return _encrypt_cbc(plaintext, encryption_type=encryption_type, key=settings.key)
    raise ValueError(f"Unsupported encryption type: {encryption_type}")


def encrypt_sensitive_query_params(
    params: dict[str, Any],
    *,
    intent_id: str,
    settings: FieldEncryptionSettings,
) -> dict[str, Any]:
    if not settings.active or intent_id not in _SENSITIVE_QUERY_INTENTS:
        return params
    if intent_id == "payment_phone_lookup":
        phone = params.get("phone")
        if not isinstance(phone, str) or not phone.strip():
            return params
        if is_stored_ciphertext(phone):
            return params
        return {**params, "phone": encrypt_field_value(phone, settings)}
    return params
