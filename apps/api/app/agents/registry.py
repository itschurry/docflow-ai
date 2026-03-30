"""Agent registry: loads agents.yaml and builds agent instances."""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

import yaml

from app.agents.base import AgentConfig, BaseAgent
from app.agents.coder import make_coder
from app.agents.critic import make_critic
from app.agents.manager import make_manager
from app.agents.planner import make_planner
from app.agents.qa import make_qa
from app.agents.writer import make_writer

if TYPE_CHECKING:
    pass

_FACTORIES = {
    "planner": make_planner,
    "writer": make_writer,
    "critic": make_critic,
    "coder": make_coder,
    "qa": make_qa,
    "manager": make_manager,
}

# Pipeline execution order
PIPELINE_ORDER = ["planner", "writer", "critic", "qa", "manager"]


def load_agent_registry(config_path: str) -> dict[str, BaseAgent]:
    if not os.path.exists(config_path):
        return {}
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    agents: dict[str, BaseAgent] = {}
    for handle, cfg in raw.get("agents", {}).items():
        if not cfg.get("enabled", True):
            continue
        provider = _resolve_agent_provider(handle, cfg)
        model = _resolve_agent_model(handle, cfg, provider)
        agent_cfg = AgentConfig(
            handle=handle,
            display_name=cfg.get("display_name", handle.capitalize()),
            emoji=cfg.get("emoji", "🤖"),
            identity=str(cfg.get("identity", handle)).strip() or handle,
            provider=provider,
            model=model,
            max_tokens=cfg.get("max_tokens", 1500),
            system_prompt=cfg.get("system_prompt", "").strip(),
            enabled=cfg.get("enabled", True),
        )
        factory = _FACTORIES.get(handle)
        if factory:
            agents[handle] = factory(agent_cfg)
    return agents


def _resolve_env(value: str) -> str:
    """Replace ${VAR_NAME} with environment variable value."""
    def replacer(m: re.Match) -> str:
        return os.environ.get(m.group(1), "")
    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def _agent_env_name(handle: str, suffix: str) -> str:
    return f"AGENT_{handle.strip().upper()}_{suffix.strip().upper()}"


def _agent_env_value(handle: str, suffix: str) -> str:
    return _resolve_env(os.environ.get(_agent_env_name(handle, suffix), ""))


def _resolve_agent_provider(handle: str, cfg: dict) -> str:
    override = _agent_env_value(handle, "provider").strip().lower()
    if override:
        return override
    provider = _resolve_env(str(cfg.get("provider", "stub"))).strip().lower()
    return provider or "stub"


def _default_model_for_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == "openai":
        return os.environ.get("OPENAI_MODEL", "")
    if normalized == "anthropic":
        return os.environ.get("ANTHROPIC_MODEL", "")
    if normalized == "ollama":
        return os.environ.get("OLLAMA_MODEL", "")
    return ""


def _resolve_agent_model(handle: str, cfg: dict, provider: str) -> str:
    override = _agent_env_value(handle, "model").strip()
    if override:
        return override

    provider_override = _agent_env_value(handle, "provider").strip()
    if provider_override:
        return _default_model_for_provider(provider)

    model = _resolve_env(str(cfg.get("model", ""))).strip()
    if model:
        return model
    return _default_model_for_provider(provider)
