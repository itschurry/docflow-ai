"""Telegram Bot API client using httpx."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(timeout=30.0)

    def _url(self, method: str) -> str:
        return _BASE.format(token=self.token, method=method)

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str = "HTML",
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send a message to Telegram. Returns telegram message_id or None."""
        if not self.token:
            logger.debug("Telegram token not configured, skipping send_message")
            return None

        # Telegram message limit is 4096 chars
        if len(text) > 4096:
            text = text[:4090] + "…"

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        if message_thread_id:
            payload["message_thread_id"] = message_thread_id

        try:
            resp = await self._client.post(self._url("sendMessage"), json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", {}).get("message_id")
        except Exception as exc:
            logger.error("Telegram sendMessage failed: %s", exc)
            return None

    async def set_webhook(self, webhook_url: str, secret_token: str = "") -> bool:
        if not self.token:
            return False
        payload: dict[str, Any] = {"url": webhook_url}
        if secret_token:
            payload["secret_token"] = secret_token
        try:
            resp = await self._client.post(self._url("setWebhook"), json=payload)
            resp.raise_for_status()
            return resp.json().get("ok", False)
        except Exception as exc:
            logger.error("Telegram setWebhook failed: %s", exc)
            return False

    async def delete_webhook(self) -> bool:
        if not self.token:
            return False
        try:
            resp = await self._client.post(self._url("deleteWebhook"))
            return resp.json().get("ok", False)
        except Exception as exc:
            logger.error("Telegram deleteWebhook failed: %s", exc)
            return False

    async def get_me(self) -> dict:
        if not self.token:
            return {}
        try:
            resp = await self._client.get(self._url("getMe"))
            return resp.json().get("result", {})
        except Exception as exc:
            logger.error("Telegram getMe failed: %s", exc)
            return {}

    async def aclose(self) -> None:
        await self._client.aclose()


# Singleton bot instance
bot = TelegramBot(token=settings.telegram_bot_token)
