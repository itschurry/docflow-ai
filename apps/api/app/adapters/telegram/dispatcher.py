"""Dispatcher: resolves role → identity, formats with mentions, sends via MultiBotOutbound."""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import yaml

from app.adapters.telegram.outbound import MultiBotOutbound
from app.adapters.telegram.registry import BotRegistry
from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    identity: str
    telegram_message_id: int | None
    rendered_text: str


class BotDispatcher:
    def __init__(self, registry: BotRegistry, outbound: MultiBotOutbound):
        self._registry = registry
        self._outbound = outbound
        self._role_to_identity: dict[str, str] = {}
        self._loaded = False

    def load(self, config_path: str) -> None:
        if not os.path.exists(config_path):
            return
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        for handle, cfg in raw.get("agents", {}).items():
            identity = cfg.get("identity", handle)
            self._role_to_identity[handle] = identity
        self._loaded = True

    def resolve_identity(self, role: str) -> str:
        if not self._loaded:
            self.load(settings.agent_config_path)
        return self._role_to_identity.get(role, role)

    def build_message(
        self,
        role: str,
        body: str,
        next_role: str | None = None,
    ) -> str:
        """Format agent message body with optional @next_bot mention appended."""
        identity = self.resolve_identity(role)
        bot = self._registry.get(identity)
        emoji = bot.emoji if bot else "🤖"
        display = bot.display_name if bot else role.capitalize()

        header = f"<b>[{emoji} {display}]</b>"
        escaped_body = _escape_html(body)
        parts = [f"{header}\n{escaped_body}"]

        if next_role:
            next_identity = self.resolve_identity(next_role)
            next_username = self._registry.username_for(next_identity)
            parts.append(f"\n{next_username} 다음 이어서 진행해줘.")

        return "\n".join(parts)

    async def dispatch(
        self,
        role: str,
        chat_id: str | int,
        body: str,
        next_role: str | None = None,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> DispatchResult:
        """Format + send via the correct bot identity. Returns DispatchResult."""
        identity = self.resolve_identity(role)
        rendered = self.build_message(role, body, next_role)
        tg_msg_id = await self._outbound.send_message_as(
            identity=identity,
            chat_id=chat_id,
            text=rendered,
            reply_to_message_id=reply_to_message_id,
            message_thread_id=message_thread_id,
        )
        return DispatchResult(
            identity=identity,
            telegram_message_id=tg_msg_id,
            rendered_text=rendered,
        )

    async def dispatch_status(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        """Send a plain status message via a specific identity."""
        return await self._outbound.send_message_as(
            identity=identity,
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
        )


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
