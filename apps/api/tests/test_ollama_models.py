"""Tests for runtime Ollama model discovery."""

import asyncio
import sys
import types


# Stub httpx before importing config to avoid dependency on external package.
httpx_module = types.ModuleType("httpx")
httpx_module.AsyncClient = object
sys.modules.setdefault("httpx", httpx_module)

from vivian_api import config


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, _url: str, timeout: float = 0):
        return self._response


def test_get_ollama_models_reads_tags(monkeypatch):
    response = _FakeResponse(
        200,
        {
            "models": [
                {"name": "llama3.2:3b"},
                {"name": "qwen2.5:latest"},
                {"missing": "ignored"},
            ]
        },
    )

    monkeypatch.setattr(config.httpx, "AsyncClient", lambda: _FakeAsyncClient(response))

    models = asyncio.run(config.get_ollama_models())

    assert models == [
        {"id": "ollama/llama3.2:3b", "name": "llama3.2:3b", "provider": "Ollama"},
        {"id": "ollama/qwen2.5:latest", "name": "qwen2.5:latest", "provider": "Ollama"},
    ]


def test_get_available_models_includes_runtime_ollama(monkeypatch):
    async def fake_get_ollama_models():
        return [{"id": "ollama/test-model", "name": "test-model", "provider": "Ollama"}]

    monkeypatch.setattr(config, "get_ollama_models", fake_get_ollama_models)

    models = asyncio.run(config.get_available_models())

    assert any(model["id"] == "ollama/test-model" for model in models)
    assert any(model["provider"] == "OpenRouter" for model in models)
