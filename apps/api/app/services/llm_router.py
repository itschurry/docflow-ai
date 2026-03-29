from app.core.config import settings
from app.services.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    OllamaProvider,
    OpenAIProvider,
    StubLLMProvider,
)


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(api_key=settings.openai_api_key, model=settings.openai_model)

    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    if settings.llm_provider == "ollama" and settings.ollama_model:
        return OllamaProvider(model=settings.ollama_model, host=settings.ollama_host)

    # Safe fallback when provider setting or keys are missing.
    return StubLLMProvider()
