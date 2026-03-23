"""Bot identity registry — loads telegram.bots from agents.yaml."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml


@dataclass
class BotIdentity:
    key: str           # e.g. "pm", "writer"
    token: str
    username: str
    display_name: str
    emoji: str = "🤖"


class BotRegistry:
    def __init__(self):
        self._bots: dict[str, BotIdentity] = {}
        self._inbound_identity: str = "pm"

    def load(self, config_path: str) -> None:
        if not os.path.exists(config_path):
            return
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        tg_cfg = raw.get("telegram", {})
        self._inbound_identity = tg_cfg.get("inbound_identity", "pm")

        for key, cfg in tg_cfg.get("bots", {}).items():
            token = _resolve_env(cfg.get("token", ""))
            if not token:
                continue
            self._bots[key] = BotIdentity(
                key=key,
                token=token,
                username=cfg.get("username", f"{key}_bot"),
                display_name=cfg.get("display_name", key.capitalize()),
                emoji=cfg.get("emoji", "🤖"),
            )

    def get(self, identity: str) -> BotIdentity | None:
        return self._bots.get(identity)

    def get_inbound(self) -> BotIdentity | None:
        return self._bots.get(self._inbound_identity)

    @property
    def inbound_identity(self) -> str:
        return self._inbound_identity

    def all(self) -> list[BotIdentity]:
        return list(self._bots.values())

    def username_for(self, identity: str) -> str:
        bot = self._bots.get(identity)
        return f"@{bot.username}" if bot else f"@{identity}_bot"

    def has_multi_bot(self) -> bool:
        """True if more than one bot token is configured."""
        return len(self._bots) > 1


def _resolve_env(value: str) -> str:
    """Replace ${VAR_NAME} with the environment variable value."""
    def replacer(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{([^}]+)\}", replacer, value)
