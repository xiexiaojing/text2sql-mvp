from __future__ import annotations

import os
from typing import Any

import httpx


class FeishuWebhookError(RuntimeError):
    pass


def feishu_webhook_url() -> str | None:
    return os.getenv("TEXT2SQL_FEISHU_WEBHOOK", "").strip() or None


def feishu_keyword() -> str:
    return os.getenv("TEXT2SQL_FEISHU_KEYWORD", "通知").strip() or "通知"


def send_text_message(
    text: str,
    *,
    webhook_url: str | None = None,
    keyword: str | None = None,
    timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    url = webhook_url or feishu_webhook_url()
    if not url:
        raise FeishuWebhookError("TEXT2SQL_FEISHU_WEBHOOK is not configured")

    prefix = keyword if keyword is not None else feishu_keyword()
    payload = {
        "msg_type": "text",
        "content": {
            "text": f"{prefix}\n{text}" if prefix else text,
        },
    }
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(url, json=payload)
        response.raise_for_status()
        body = response.json()
    if body.get("code") not in (None, 0):
        raise FeishuWebhookError(f"Feishu webhook rejected message: {body}")
    return body
