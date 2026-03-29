from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import MagicMock

from app.services.llm_provider import OllamaProvider


class patch_modules:
    def __init__(self, **modules: object) -> None:
        self._modules = modules
        self._originals: dict[str, object | None] = {}

    def __enter__(self) -> None:
        for name, module in self._modules.items():
            self._originals[name] = sys.modules.get(name)
            sys.modules[name] = module

    def __exit__(self, exc_type, exc, tb) -> None:
        for name, module in self._originals.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def test_ollama_provider_generates_text():
    fake_client = MagicMock()
    fake_client.chat.return_value = {"message": {"content": "hello"}}
    fake_module = types.SimpleNamespace(Client=lambda host=None: fake_client)

    with patch_modules(ollama=fake_module):
        provider = OllamaProvider(model="qwen-test", host="http://localhost:11434")
        result = asyncio.run(provider.generate_text("say hello"))

    assert result == "hello"
    fake_client.chat.assert_called_once()


def test_ollama_provider_generates_structured_json():
    fake_client = MagicMock()
    fake_client.chat.return_value = {"message": {"content": '{"ok": true}'}}
    fake_module = types.SimpleNamespace(Client=lambda host=None: fake_client)

    with patch_modules(ollama=fake_module):
        provider = OllamaProvider(model="qwen-test", host="http://localhost:11434")
        result = asyncio.run(
            provider.generate_structured(
                prompt="return ok",
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            )
        )

    assert result == {"ok": True}
