from __future__ import annotations

from typing import Any

# Demo payment order status codes (align with configs/business_semantics.yaml examples)
PAYMENT_STATUS_LABELS: dict[str, str] = {
    "0": "待支付",
    "1": "支付成功",
    "2": "支付失败",
    "3": "已关闭",
    "SUCCESS": "支付成功",
    "FAILED": "支付失败",
    "CLOSED": "已关闭",
}


def _normalize_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = int(float(text))
        if float(text) == number:
            return str(number)
    except (TypeError, ValueError):
        pass
    return text


def resolve_payment_status(value: Any) -> str:
    code = _normalize_code(value)
    if code is None:
        return "未知状态"
    return PAYMENT_STATUS_LABELS.get(code, str(value))


def resolve_grouped_label(column: str, value: Any) -> Any:
    if column == "status_value":
        return resolve_payment_status(value)
    return value
