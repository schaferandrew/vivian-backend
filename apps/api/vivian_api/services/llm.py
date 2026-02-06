"""LLM service for OpenRouter API integration."""

import httpx
from vivian_api.config import Settings

settings = Settings()


async def get_chat_completion(messages: list[dict]) -> str:
    """
    Get chat completion from OpenRouter API.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        
    Returns:
        Response text from the LLM
    """
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:3000",
        "X-Title": "Vivian Chat",
    }
    
    payload = {
        "model": settings.selected_model,
        "messages": messages,
    }
    
    async with httpx.AsyncClient() as client:
        print(f"OpenRouter URL: {settings.openrouter_base_url}/chat/completions")
        print(f"Model: {settings.openrouter_model}")
        print(f"API Key (first 10 chars): {settings.openrouter_api_key[:10]}...")
        
        response = await client.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60.0
        )
        
        print(f"OpenRouter response status: {response.status_code}")
        print(f"OpenRouter response body: {response.text}")
        
        response.raise_for_status()
        data = response.json()
        
        return data["choices"][0]["message"]["content"]
