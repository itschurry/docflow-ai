from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.llm_provider import (
    AnthropicProvider,
    LLMProvider,
    OpenAIProvider,
    StubLLMProvider,
)
from app.core.config import settings


@dataclass
class AgentConfig:
    handle: str
    display_name: str
    emoji: str
    provider: str
    model: str
    max_tokens: int
    system_prompt: str
    enabled: bool = True


@dataclass
class AgentResult:
    handle: str
    display_name: str
    emoji: str
    text: str
    provider: str
    model: str


def build_provider(provider: str, model: str) -> LLMProvider:
    if provider == "openai" and settings.openai_api_key:
        return OpenAIProvider(api_key=settings.openai_api_key, model=model)
    if provider == "anthropic" and settings.anthropic_api_key:
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)
    return StubLLMProvider()


class BaseAgent(ABC):
    def __init__(self, config: AgentConfig):
        self.config = config
        self._provider = build_provider(config.provider, config.model)

    @property
    def handle(self) -> str:
        return self.config.handle

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def emoji(self) -> str:
        return self.config.emoji

    @abstractmethod
    def build_prompt(self, user_request: str, context: str = "") -> str:
        """Build the full prompt for this agent turn."""

    async def run(self, user_request: str, context: str = "") -> AgentResult:
        prompt = self.build_prompt(user_request, context)
        full_prompt = f"{self.config.system_prompt}\n\n{prompt}"
        text = await self._provider.generate_text(full_prompt)
        return AgentResult(
            handle=self.config.handle,
            display_name=self.config.display_name,
            emoji=self.config.emoji,
            text=text.strip(),
            provider=self.config.provider,
            model=self.config.model,
        )
