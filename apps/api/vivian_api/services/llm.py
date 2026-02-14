"""LLM service for OpenRouter and Ollama integration."""

import logging
import time

import httpx

from vivian_api.config import Settings, get_selected_model, AVAILABLE_MODELS, get_ollama_base_url
from vivian_api.logging_service import log_with_context

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


async def get_chat_completion(messages: list[dict], web_search_enabled: bool = False) -> str:
    """
    Get chat completion from either OpenRouter or Ollama.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        web_search_enabled: Whether to enable web search (~$0.02/query)
        
    Returns:
        Response text from the LLM
    """
    model = get_selected_model()
    
    if _is_ollama_model(model):
        return await _get_ollama_completion(messages, model)
    else:
        return await _get_openrouter_completion(messages, model, web_search_enabled)


async def _get_openrouter_completion(messages: list[dict], model: str, web_search_enabled: bool = False) -> str:
    """Get chat completion from OpenRouter API."""
    settings = Settings()
    start_time = time.time()

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

    log_with_context(
        logger,
        "DEBUG",
        "Calling OpenRouter API",
        service="llm",
        model=model,
        message_count=len(messages),
        web_search_enabled=web_search_enabled,
    )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0
        )
        
        duration_ms = (time.time() - start_time) * 1000

        if response.status_code == 402:
            try:
                body = response.json()
                msg = (
                    (body.get("error") or {}).get("message")
                    or "Your account or API key has insufficient credits. Add more credits and retry."
                )
            except Exception:
                msg = "Your account or API key has insufficient credits. Add more credits and retry."
            log_with_context(
                logger,
                "ERROR",
                "OpenRouter credits error",
                service="llm",
                model=model,
                duration_ms=round(duration_ms, 2),
                status_code=402,
            )
            raise OpenRouterCreditsError(msg)

        if response.status_code == 429:
            try:
                body = response.json()
                base_msg = (
                    (body.get("error") or {}).get("message")
                    or "Rate limit exceeded"
                )
                msg = f"{base_msg} for {model}. Free models have strict rate limits. Try again in a few moments or switch to a paid model."
            except Exception:
                msg = f"Rate limit exceeded for {model}. Free models have strict rate limits. Try again in a few moments or switch to a paid model."
            log_with_context(
                logger,
                "WARNING",
                "OpenRouter rate limit exceeded",
                service="llm",
                model=model,
                duration_ms=round(duration_ms, 2),
                status_code=429,
            )
            raise OpenRouterRateLimitError(msg)

        if response.status_code == 404:
            try:
                body = response.json()
                msg = (
                    (body.get("error") or {}).get("message")
                    or "Model not found or unavailable."
                )
            except Exception:
                msg = "Model not found or unavailable."
            log_with_context(
                logger,
                "ERROR",
                "OpenRouter model not found",
                service="llm",
                model=model,
                duration_ms=round(duration_ms, 2),
                status_code=404,
            )
            raise OpenRouterCreditsError(f"Model error: {msg}")

        response.raise_for_status()
        data = response.json()
        
        log_with_context(
            logger,
            "DEBUG",
            "OpenRouter API call completed",
            service="llm",
            model=model,
            duration_ms=round(duration_ms, 2),
            status_code=response.status_code,
        )

        return data["choices"][0]["message"]["content"]


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


async def _get_ollama_completion(messages: list[dict], model: str) -> str:
    """Get chat completion from Ollama local API."""
    ollama_url = get_ollama_base_url()
    # Strip "ollama/" prefix if present
    ollama_model = model.replace("ollama/", "")
    start_time = time.time()
    
    payload = {
        "model": ollama_model,
        "messages": messages,
        "stream": False,
    }
    
    # Ollama can take a long time on first request while loading model into
    # memory, especially on low-VRAM machines. Use a generous timeout.
    timeout = httpx.Timeout(300.0, connect=10.0)

    log_with_context(
        logger,
        "DEBUG",
        "Calling Ollama API",
        service="llm",
        model=ollama_model,
        message_count=len(messages),
    )

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{ollama_url}/api/chat",
                json=payload,
                timeout=timeout,
            )
            duration_ms = (time.time() - start_time) * 1000
        except httpx.TimeoutException:
            duration_ms = (time.time() - start_time) * 1000
            log_with_context(
                logger,
                "ERROR",
                "Ollama timeout",
                service="llm",
                model=ollama_model,
                duration_ms=round(duration_ms, 2),
            )
            raise OllamaTimeoutError(ollama_model, timeout.read or 300.0)
        except httpx.ConnectError:
            duration_ms = (time.time() - start_time) * 1000
            log_with_context(
                logger,
                "ERROR",
                "Ollama connection error",
                service="llm",
                model=ollama_model,
                duration_ms=round(duration_ms, 2),
            )
            raise OllamaConnectionError(ollama_model, "Is Ollama running?")
        
        response.raise_for_status()
        data = response.json()
        
        log_with_context(
            logger,
            "DEBUG",
            "Ollama API call completed",
            service="llm",
            model=ollama_model,
            duration_ms=round(duration_ms, 2),
            status_code=response.status_code,
        )
        
        return data["message"]["content"]
