"""Agent registry: loads agents.yaml and builds agent instances."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import yaml

from app.agents.base import AgentConfig, BaseAgent
from app.agents.coder import make_coder
from app.agents.critic import make_critic
from app.agents.manager import make_manager
from app.agents.planner import make_planner
from app.agents.reviewer import make_reviewer
from app.agents.writer import make_writer

if TYPE_CHECKING:
    pass

_FACTORIES = {
    "planner": make_planner,
    "writer": make_writer,
    "critic": make_critic,
    "coder": make_coder,
    "reviewer": make_reviewer,
    "manager": make_manager,
}

# Pipeline execution order
PIPELINE_ORDER = ["planner", "writer", "critic", "reviewer", "manager"]


def load_agent_registry(config_path: str) -> dict[str, BaseAgent]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    agents: dict[str, BaseAgent] = {}
    for handle, cfg in raw.get("agents", {}).items():
        if not cfg.get("enabled", True):
            continue
        agent_cfg = AgentConfig(
            handle=handle,
            display_name=cfg.get("display_name", handle.capitalize()),
            emoji=cfg.get("emoji", "🤖"),
            provider=cfg.get("provider", "stub"),
            model=cfg.get("model", ""),
            max_tokens=cfg.get("max_tokens", 1500),
            system_prompt=cfg.get("system_prompt", "").strip(),
            enabled=cfg.get("enabled", True),
        )
        factory = _FACTORIES.get(handle)
        if factory:
            agents[handle] = factory(agent_cfg)
    return agents
