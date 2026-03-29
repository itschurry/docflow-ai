from abc import ABC, abstractmethod
import asyncio
import json

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI


class LLMProvider(ABC):
    @abstractmethod
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def generate_text(self, prompt: str) -> str:
        raise NotImplementedError


class StubLLMProvider(LLMProvider):
    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        return {"prompt": prompt, "schema": schema}

    async def generate_text(self, prompt: str) -> str:
        return f"stub-response: {prompt}"


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        msg = (
            "Return JSON only. Follow schema exactly.\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
            f"Prompt: {prompt}"
        )
        response = await self.client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": msg}],
        )
        text = response.output_text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def generate_text(self, prompt: str) -> str:
        response = await self.client.responses.create(
            model=self.model,
            input=[{"role": "user", "content": prompt}],
        )
        return response.output_text


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        msg = (
            "Return JSON only. Follow schema exactly.\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
            f"Prompt: {prompt}"
        )
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            messages=[{"role": "user", "content": msg}],
        )
        text = "".join(block.text for block in response.content if getattr(
            block, "text", None)).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def generate_text(self, prompt: str) -> str:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if getattr(block, "text", None)).strip()


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, host: str):
        try:
            from ollama import Client
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "Ollama provider requires the 'ollama' Python package to be installed."
            ) from exc

        self.model = model
        try:
            self.client = Client(host=host)
        except TypeError:
            self.client = Client()

    async def generate_structured(self, prompt: str, schema: dict) -> dict:
        payload = (
            "Return JSON only. Follow schema exactly.\n"
            f"Schema: {json.dumps(schema, ensure_ascii=False)}\n"
            f"Prompt: {prompt}"
        )
        response = await asyncio.to_thread(
            self.client.chat,
            model=self.model,
            messages=[{"role": "user", "content": payload}],
            format="json",
        )
        text = self._extract_content(response).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    async def generate_text(self, prompt: str) -> str:
        response = await asyncio.to_thread(
            self.client.chat,
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return self._extract_content(response).strip()

    @staticmethod
    def _extract_content(response: object) -> str:
        if isinstance(response, dict):
            message = response.get("message", {})
            return str(message.get("content", "") or "")

        message = getattr(response, "message", None)
        if isinstance(message, dict):
            return str(message.get("content", "") or "")
        return str(getattr(message, "content", "") or "")
