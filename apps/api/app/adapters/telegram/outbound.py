"""Multi-bot outbound layer — sends messages via the correct bot token per identity."""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.adapters.telegram.registry import BotIdentity, BotRegistry
from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org/bot{token}/{method}"


class MultiBotOutbound:
    def __init__(self, registry: BotRegistry):
        self._registry = registry
        self._client = httpx.AsyncClient(timeout=30.0)

    async def send_message_as(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        reply_to_message_id: int | None = None,
        parse_mode: str = "HTML",
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send a message using the bot token mapped to `identity`. Returns telegram message_id."""
        bot = self._registry.get(identity)
        if not bot:
            # Fallback: try inbound bot token
            bot = self._registry.get_inbound()
        if not bot or not bot.token:
            logger.warning("No token for identity '%s', skipping send", identity)
            return None
        return await self._send(
            bot.token, chat_id, text, reply_to_message_id, parse_mode, message_thread_id
        )

    async def _send(
        self,
        token: str,
        chat_id: str | int,
        text: str,
        reply_to_message_id: int | None,
        parse_mode: str,
        message_thread_id: int | None,
    ) -> int | None:
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

        url = _BASE.format(token=token, method="sendMessage")
        try:
            resp = await self._client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("result", {}).get("message_id")
            logger.debug("Sent message via token …%s, msg_id=%s", token[-6:], msg_id)
            return msg_id
        except Exception as exc:
            logger.error("sendMessage failed (identity=%s): %s", "?", exc)
            return None

    async def aclose(self) -> None:
        await self._client.aclose()
