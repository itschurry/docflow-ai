from app.core.config import settings
from app.services import llm_router
from app.services.llm_provider import OllamaProvider, OpenAIProvider, StubLLMProvider


def test_llm_router_returns_stub_when_provider_config_is_incomplete(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "")

    provider = llm_router.get_llm_provider()

    assert isinstance(provider, StubLLMProvider)


def test_llm_router_returns_openai_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "openai_model", "gpt-test")

    provider = llm_router.get_llm_provider()

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-test"


def test_llm_router_returns_ollama_provider(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "ollama")
    monkeypatch.setattr(settings, "ollama_host", "http://localhost:11434")
    monkeypatch.setattr(settings, "ollama_model", "qwen-test")

    provider = llm_router.get_llm_provider()

    assert isinstance(provider, OllamaProvider)
    assert provider.model == "qwen-test"
