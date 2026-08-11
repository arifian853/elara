"""
services/bridge.py — Telegram Bot API outbound bridge.

Sends messages to Arifian's personal Telegram DM
when a project request is submitted via the intake flow.
Uses httpx (async) — no telegram library needed.
"""

from __future__ import annotations

import html as html_lib
import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def send_telegram_message(
    text: str,
    chat_id: str | None = None,
    parse_mode: str = "HTML",
) -> bool:
    """
    Send a message to Telegram via Bot API.

    Args:
        text: Message text to send.
        chat_id: Target chat ID (defaults to OWNER_CHAT_ID from env).
        parse_mode: Telegram parse mode ("HTML" or "Markdown").

    Returns:
        True if sent successfully, False otherwise.
    """
    if not settings.telegram_bot_token:
        logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping notification")
        return False

    target_chat_id = chat_id or settings.owner_chat_id
    if not target_chat_id:
        logger.warning("OWNER_CHAT_ID not configured, skipping notification")
        return False

    url = TELEGRAM_API_URL.format(token=settings.telegram_bot_token)
    text = html_lib.escape(text)
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json=payload)

        if response.status_code == 200:
            logger.info(f"Telegram message sent to {target_chat_id}")
            return True
        else:
            logger.error(
                f"Telegram API error {response.status_code}: {response.text}"
            )
            return False

    except Exception as e:
        logger.exception(f"Telegram send failed: {e}")
        return False
