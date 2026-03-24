"""Multi-bot outbound layer — sends messages via the correct bot token per identity."""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import logging
import time
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
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_sent_at: dict[str, float] = defaultdict(float)
        self._burst_windows: dict[str, deque[float]] = defaultdict(deque)

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
        await self._throttle(identity)
        return await self._send(
            identity=identity,
            token=bot.token,
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode,
            message_thread_id=message_thread_id,
        )

    async def _throttle(self, identity: str) -> None:
        lock = self._locks[identity]
        async with lock:
            now = time.monotonic()
            cooldown = settings.telegram_send_cooldown_seconds
            last_sent = self._last_sent_at[identity]
            wait = cooldown - (now - last_sent)
            if wait > 0:
                await asyncio.sleep(wait)
                now = time.monotonic()

            win = self._burst_windows[identity]
            window_sec = settings.telegram_identity_burst_window_seconds
            while win and now - win[0] > window_sec:
                win.popleft()
            if len(win) >= settings.telegram_identity_burst_limit:
                burst_wait = window_sec - (now - win[0])
                if burst_wait > 0:
                    await asyncio.sleep(burst_wait)
                    now = time.monotonic()
                while win and now - win[0] > window_sec:
                    win.popleft()
            win.append(now)
            self._last_sent_at[identity] = now

    async def _send(
        self,
        identity: str,
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
        for attempt in range(1, 4):
            try:
                resp = await self._client.post(url, json=payload)
                data = resp.json()
                if resp.status_code == 429:
                    retry_after = (
                        data.get("parameters", {}).get("retry_after")
                        or int(resp.headers.get("Retry-After", "1") or "1")
                    )
                    retry_after = max(1, int(retry_after))
                    logger.warning(
                        "Telegram 429 (identity=%s, attempt=%s), retry_after=%ss",
                        identity, attempt, retry_after,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                if resp.status_code == 400:
                    logger.error(
                        "Telegram 400 (identity=%s) payload(chat_id=%s, reply_to=%s, parse_mode=%s, text_len=%s) body=%s",
                        identity,
                        chat_id,
                        reply_to_message_id,
                        parse_mode,
                        len(text),
                        data,
                    )
                    return None
                resp.raise_for_status()
                msg_id = data.get("result", {}).get("message_id")
                logger.debug("Sent message via identity=%s token …%s, msg_id=%s", identity, token[-6:], msg_id)
                return msg_id
            except Exception as exc:
                logger.error("sendMessage failed (identity=%s, attempt=%s): %s", identity, attempt, exc)
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)
                else:
                    return None
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
