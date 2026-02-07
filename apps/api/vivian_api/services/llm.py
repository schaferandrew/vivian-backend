"""LLM service for OpenRouter and Ollama integration."""

import httpx
from vivian_api.config import Settings, get_selected_model


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


async def get_chat_completion(messages: list[dict]) -> str:
    """
    Get chat completion from either OpenRouter or Ollama.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        
    Returns:
        Response text from the LLM
    """
    model = get_selected_model()
    
    if model.startswith("ollama/"):
        return await _get_ollama_completion(messages, model)
    else:
        return await _get_openrouter_completion(messages, model)


async def _get_openrouter_completion(messages: list[dict], model: str) -> str:
    """Get chat completion from OpenRouter API."""
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
    }
    
    async with httpx.AsyncClient() as client:
        print(f"OpenRouter URL: {settings.openrouter_base_url}/chat/completions")
        print(f"Model: {model}")
        print(f"API Key (first 10 chars): {settings.openrouter_api_key[:10]}...")
        
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0
        )
        
        print(f"OpenRouter response status: {response.status_code}")

        if response.status_code == 402:
            try:
                body = response.json()
                msg = (
                    (body.get("error") or {}).get("message")
                    or "Your account or API key has insufficient credits. Add more credits and retry."
                )
            except Exception:
                msg = "Your account or API key has insufficient credits. Add more credits and retry."
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
            raise OpenRouterCreditsError(f"Model error: {msg}")

        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"]


async def _get_ollama_completion(messages: list[dict], model: str) -> str:
    """Get chat completion from Ollama local API."""
    ollama_url = Settings.get_ollama_base_url()
    ollama_model = model.replace("ollama/", "")
    
    payload = {
        "model": ollama_model,
        "messages": messages,
        "stream": False,
    }
    
    async with httpx.AsyncClient() as client:
        print(f"Ollama URL: {ollama_url}/api/chat")
        print(f"Ollama Model: {ollama_model}")
        
        response = await client.post(
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=120.0
        )
        
        print(f"Ollama response status: {response.status_code}")
        
        response.raise_for_status()
        data = response.json()
        
        return data["message"]["content"]
