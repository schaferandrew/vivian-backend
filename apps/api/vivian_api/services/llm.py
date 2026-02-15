"""LLM service for OpenRouter and Ollama integration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from vivian_api.config import Settings, get_selected_model, AVAILABLE_MODELS, get_ollama_base_url


logger = logging.getLogger(__name__)


def _is_ollama_model(model_id: str) -> bool:
    """Check if a model ID corresponds to an Ollama model."""
    for model in AVAILABLE_MODELS:
        if model["id"] == model_id:
            return model.get("provider") == "Ollama"
    return False


class OpenRouterCreditsError(Exception):
    """Raised when OpenRouter returns 402 (insufficient credits)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class OpenRouterRateLimitError(Exception):
    """Raised when OpenRouter returns 429 (rate limit exceeded)."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ModelToolCallingUnsupportedError(Exception):
    """Raised when the selected model/provider rejects tool-calling payloads."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class LLMToolCall:
    """Normalized tool call emitted by a model completion."""

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str

    def as_openai_dict(self) -> dict[str, Any]:
        """Serialize into OpenAI-compatible tool_call message shape."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments or "{}",
            },
        }


@dataclass(frozen=True)
class ChatCompletionResult:
    """Normalized completion payload with optional model tool calls."""

    content: str
    tool_calls: list[LLMToolCall]


async def get_chat_completion(messages: list[dict], web_search_enabled: bool = False) -> str:
    """
    Get chat completion from either OpenRouter or Ollama.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        web_search_enabled: Whether to enable web search (~$0.02/query)
        
    Returns:
        Response text from the LLM
    """
    result = await get_chat_completion_result(
        messages,
        web_search_enabled=web_search_enabled,
        tools=None,
    )
    return result.content


def _extract_text_content(content: Any) -> str:
    """Normalize provider content payloads into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    chunks.append(text)
                elif text is not None:
                    chunks.append(str(text))
            elif part is not None:
                chunks.append(str(part))
        return "".join(chunks).strip()
    return str(content)


def _parse_tool_arguments(raw_arguments: Any) -> tuple[dict[str, Any], str]:
    """Parse OpenAI-style tool argument JSON string into a dictionary."""
    if isinstance(raw_arguments, dict):
        return raw_arguments, json.dumps(raw_arguments, separators=(",", ":"))

    if not isinstance(raw_arguments, str):
        return {}, "{}"

    trimmed = raw_arguments.strip()
    if not trimmed:
        return {}, "{}"

    try:
        parsed = json.loads(trimmed)
    except json.JSONDecodeError:
        logger.warning("llm.tool_argument_parse_failed raw=%s", trimmed[:300])
        return {}, "{}"

    if isinstance(parsed, dict):
        return parsed, trimmed

    logger.warning("llm.tool_argument_not_object raw=%s", trimmed[:300])
    return {}, "{}"


def _parse_tool_calls(raw_message: dict[str, Any]) -> list[LLMToolCall]:
    """Normalize provider tool_calls payload to internal representation."""
    raw_tool_calls = raw_message.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return []

    tool_calls: list[LLMToolCall] = []
    for index, raw_call in enumerate(raw_tool_calls):
        if not isinstance(raw_call, dict):
            continue
        raw_function = raw_call.get("function")
        if not isinstance(raw_function, dict):
            continue

        name = str(raw_function.get("name") or "").strip()
        if not name:
            continue

        arguments, raw_arguments = _parse_tool_arguments(raw_function.get("arguments"))
        call_id = str(raw_call.get("id") or f"tool_call_{index}")
        tool_calls.append(
            LLMToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=raw_arguments,
            )
        )
    return tool_calls


def _extract_error_message(response: httpx.Response, fallback: str) -> str:
    """Extract API error message when present."""
    try:
        body = response.json()
    except Exception:
        return fallback
    return str((body.get("error") or {}).get("message") or fallback)


async def get_chat_completion_result(
    messages: list[dict[str, Any]],
    web_search_enabled: bool = False,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> ChatCompletionResult:
    """Get completion text plus optional tool calls for model-driven function execution."""
    model = get_selected_model()
    if _is_ollama_model(model):
        if tools:
            logger.warning(
                "llm.tools_requested_with_ollama model=%s tools=%s",
                model,
                [tool.get("function", {}).get("name") for tool in tools if isinstance(tool, dict)],
            )
        return await _get_ollama_completion_result(messages, model)
    return await _get_openrouter_completion_result(
        messages,
        model,
        web_search_enabled=web_search_enabled,
        tools=tools,
        tool_choice=tool_choice,
    )


async def _get_openrouter_completion_result(
    messages: list[dict[str, Any]],
    model: str,
    web_search_enabled: bool = False,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> ChatCompletionResult:
    """Get completion from OpenRouter with optional tool call output."""
    settings = Settings()

    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Vivian Chat",
    }

    payload = {
        "model": model,
        "messages": messages,
        "plugins": [{"id": "web"}] if web_search_enabled else [{"id": "web", "enabled": False}],
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    async with httpx.AsyncClient() as client:
        logger.info(
            "llm.openrouter.request model=%s web_search=%s tools=%s",
            model,
            web_search_enabled,
            [tool.get("function", {}).get("name") for tool in tools or [] if isinstance(tool, dict)],
        )
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0
        )
        logger.info("llm.openrouter.response_status status=%s model=%s", response.status_code, model)

        if response.status_code == 402:
            msg = _extract_error_message(
                response,
                "Your account or API key has insufficient credits. Add more credits and retry.",
            )
            raise OpenRouterCreditsError(msg)

        if response.status_code == 429:
            base_msg = _extract_error_message(response, "Rate limit exceeded")
            msg = f"{base_msg} for {model}. Free models have strict rate limits. Try again in a few moments or switch to a paid model."
            raise OpenRouterRateLimitError(msg)

        if response.status_code == 404:
            msg = _extract_error_message(response, "Model not found or unavailable.")
            raise OpenRouterCreditsError(f"Model error: {msg}")

        if response.status_code == 400 and tools:
            message = _extract_error_message(response, "Bad request")
            if "tool" in message.lower() or "function" in message.lower():
                raise ModelToolCallingUnsupportedError(
                    f"Model rejected tool-calling request: {message}"
                )

        response.raise_for_status()
        data = response.json()
        raw_message = ((data.get("choices") or [{}])[0].get("message") or {})
        content = _extract_text_content(raw_message.get("content"))
        tool_calls = _parse_tool_calls(raw_message)
        return ChatCompletionResult(content=content, tool_calls=tool_calls)


class OllamaTimeoutError(Exception):
    """Raised when Ollama takes too long to respond (model loading or inference)."""

    def __init__(self, model: str, timeout: float):
        self.model = model
        self.timeout = timeout
        super().__init__(
            f"Ollama timed out after {int(timeout)}s for model '{model}'. "
            "The model may still be loading — try again in a moment."
        )


class OllamaConnectionError(Exception):
    """Raised when Ollama is unreachable."""

    def __init__(self, model: str, detail: str = ""):
        self.model = model
        msg = f"Could not connect to Ollama for model '{model}'."
        if detail:
            msg += f" {detail}"
        super().__init__(msg)


async def _get_ollama_completion_result(
    messages: list[dict[str, Any]],
    model: str,
) -> ChatCompletionResult:
    """Get chat completion from Ollama local API."""
    ollama_url = get_ollama_base_url()
    # Strip "ollama/" prefix if present
    ollama_model = model.replace("ollama/", "")
    
    payload = {
        "model": ollama_model,
        "messages": messages,
        "stream": False,
    }
    
    # Ollama can take a long time on first request while loading model into
    # memory, especially on low-VRAM machines. Use a generous timeout.
    timeout = httpx.Timeout(300.0, connect=10.0)

    async with httpx.AsyncClient() as client:
        logger.info("llm.ollama.request model=%s", ollama_model)
        
        try:
            response = await client.post(
                f"{ollama_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
        except httpx.TimeoutException:
            raise OllamaTimeoutError(ollama_model, timeout.read or 300.0)
        except httpx.ConnectError:
            raise OllamaConnectionError(ollama_model, "Is Ollama running?")
        
        logger.info("llm.ollama.response_status status=%s model=%s", response.status_code, ollama_model)
        
        response.raise_for_status()
        data = response.json()
        raw_message = data.get("message", {}) if isinstance(data, dict) else {}
        if not isinstance(raw_message, dict):
            raw_message = {}
        content = _extract_text_content(raw_message.get("content"))
        tool_calls = _parse_tool_calls(raw_message)
        return ChatCompletionResult(content=content, tool_calls=tool_calls)
