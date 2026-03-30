from pathlib import Path

from app.agents.base import build_provider
from app.agents.registry import load_agent_registry
from app.core.config import settings
from app.services.llm_provider import OpenAIProvider


def _write_agent_config(tmp_path: Path) -> str:
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        """
agents:
  planner:
    enabled: true
    display_name: "Planner"
    emoji: "P"
    identity: planner
    provider: openai
    model: ${OPENAI_MODEL}
    max_tokens: 1000
    system_prompt: "plan"

  writer:
    enabled: true
    display_name: "Writer"
    emoji: "W"
    identity: writer
    provider: anthropic
    model: ${ANTHROPIC_MODEL}
    max_tokens: 1000
    system_prompt: "write"
""".strip(),
        encoding="utf-8",
    )
    return str(config_path)


def test_agent_registry_uses_agent_specific_provider_and_model_overrides(monkeypatch, tmp_path):
    config_path = _write_agent_config(tmp_path)
    monkeypatch.delenv("AGENT_PLANNER_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_PLANNER_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-default")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-planner")
    monkeypatch.setenv("AGENT_PLANNER_PROVIDER", "ollama")
    monkeypatch.setenv("AGENT_PLANNER_MODEL", "${OLLAMA_MODEL}")

    agents = load_agent_registry(config_path)

    assert agents["planner"].config.provider == "ollama"
    assert agents["planner"].config.model == "qwen-planner"


def test_agent_registry_uses_provider_default_model_when_only_provider_is_overridden(monkeypatch, tmp_path):
    config_path = _write_agent_config(tmp_path)
    monkeypatch.delenv("AGENT_WRITER_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_WRITER_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-router")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-router")
    monkeypatch.setenv("AGENT_WRITER_PROVIDER", "openai")

    agents = load_agent_registry(config_path)

    assert agents["writer"].config.provider == "openai"
    assert agents["writer"].config.model == "gpt-router"


def test_build_provider_uses_explicit_agent_provider_even_when_global_llm_provider_is_stub(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")

    provider = build_provider("openai", "gpt-test")

    assert isinstance(provider, OpenAIProvider)
