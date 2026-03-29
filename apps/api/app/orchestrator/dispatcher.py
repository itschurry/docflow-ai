from __future__ import annotations

from dataclasses import dataclass
from html import escape

from app.agents.base import BaseAgent


@dataclass
class DispatchResult:
    identity: str
    telegram_message_id: int | None
    rendered_text: str


@dataclass
class LocalBotInfo:
    key: str
    username: str
    display_name: str
    emoji: str


class LocalBotRegistry:
    def __init__(self, agents: dict[str, BaseAgent]):
        self._by_identity: dict[str, LocalBotInfo] = {}
        self._inbound_identity = "pm"
        for handle, agent in agents.items():
            identity = agent.config.identity or handle
            info = LocalBotInfo(
                key=identity,
                username=identity,
                display_name=agent.display_name,
                emoji=agent.emoji,
            )
            self._by_identity[identity] = info
            if handle == "planner":
                self._inbound_identity = identity

    def get(self, identity: str) -> LocalBotInfo | None:
        return self._by_identity.get(identity)

    @property
    def inbound_identity(self) -> str:
        return self._inbound_identity

    def username_for(self, identity: str) -> str:
        info = self.get(identity)
        if not info:
            return f"@{identity}"
        return f"@{info.username}"


class LocalDispatcher:
    def __init__(self, agents: dict[str, BaseAgent]):
        self._agents = agents
        self._registry = LocalBotRegistry(agents)

    def resolve_identity(self, role: str) -> str:
        agent = self._agents.get(role)
        if not agent:
            return role
        return agent.config.identity or role

    def build_message(
        self,
        role: str,
        body: str,
        next_role: str | None = None,
        include_handoff_hint: bool = True,
    ) -> str:
        identity = self.resolve_identity(role)
        info = self._registry.get(identity)
        emoji = info.emoji if info else "🤖"
        display_name = info.display_name if info else role.capitalize()

        parts = [f"<b>[{emoji} {escape(display_name)}]</b>\n{escape(body)}"]
        if next_role and include_handoff_hint:
            next_identity = self.resolve_identity(next_role)
            parts.append(f"\n{self._registry.username_for(next_identity)} 다음 이어서 진행해줘.")
        return "\n".join(parts)

    async def dispatch(
        self,
        role: str,
        chat_id: str | int,
        body: str,
        next_role: str | None = None,
        include_handoff_hint: bool = True,
        reply_to_message_id: int | None = None,
        message_thread_id: int | None = None,
    ) -> DispatchResult:
        del chat_id, reply_to_message_id, message_thread_id
        return DispatchResult(
            identity=self.resolve_identity(role),
            telegram_message_id=None,
            rendered_text=self.build_message(
                role,
                body,
                next_role=next_role,
                include_handoff_hint=include_handoff_hint,
            ),
        )

    async def dispatch_status(
        self,
        identity: str,
        chat_id: str | int,
        text: str,
        message_thread_id: int | None = None,
    ) -> int | None:
        del identity, chat_id, text, message_thread_id
        return None
