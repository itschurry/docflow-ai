"""Parse and dispatch incoming Telegram webhook updates."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.telegram.bot import bot
from app.adapters.telegram.commands import dispatch_command
from app.core.config import settings
from app.orchestrator.engine import orchestrator

logger = logging.getLogger(__name__)


def _is_allowed_chat(chat_id: int | str) -> bool:
    if not settings.telegram_allowed_chat_ids:
        return True  # No allowlist → allow all (dev mode)
    return int(chat_id) in settings.telegram_allowed_chat_ids


def _make_send_fn(chat_id: str):
    """Factory: returns a coroutine function that sends a message to Telegram."""
    async def send_fn(
        _chat_id: str,
        text: str,
        reply_to_message_id: int | None = None,
    ) -> int | None:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
    return send_fn


async def process_update(update: dict[str, Any], db: Session) -> None:
    """Entry point for all incoming Telegram updates."""
    message = update.get("message") or update.get("channel_post")
    if not message:
        return  # Ignore non-message updates (edited messages, etc.)

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", ""))
    chat_type = chat.get("type", "")
    topic_id = str(message.get("message_thread_id")) if message.get("message_thread_id") else None
    tg_message_id: int | None = message.get("message_id")
    text: str = message.get("text") or message.get("caption") or ""
    sender = message.get("from", {})
    sender_name = sender.get("username") or sender.get("first_name") or "user"

    if not chat_id or not text:
        return

    if not _is_allowed_chat(chat_id):
        logger.warning("Rejected update from disallowed chat_id=%s", chat_id)
        return

    send_fn = _make_send_fn(chat_id)

    # Route commands separately
    if text.startswith("/"):
        await dispatch_command(chat_id=chat_id, text=text, db=db)
        return

    # For private chats or group mentions: run orchestrator
    try:
        await orchestrator.process_message(
            db=db,
            chat_id=chat_id,
            text=text,
            sender_name=sender_name,
            telegram_message_id=tg_message_id,
            send_fn=send_fn,
            topic_id=topic_id,
        )
    except Exception as exc:
        logger.exception("Orchestrator error for chat_id=%s: %s", chat_id, exc)
        await send_fn(
            chat_id,
            f"❌ 처리 중 오류가 발생했습니다: {exc}",
            tg_message_id,
        )
