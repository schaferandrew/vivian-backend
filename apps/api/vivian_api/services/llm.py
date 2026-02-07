"""LLM service for OpenRouter and Ollama integration."""

import httpx
from vivian_api.config import Settings, get_selected_model


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
