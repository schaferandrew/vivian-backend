"""Vivian API configuration."""

import os
from typing import Optional
import httpx
from pydantic_settings import BaseSettings


AVAILABLE_MODELS = [
    # OpenRouter models (all non-Ollama models route through OpenRouter)
    {"id": "openrouter/free", "name": "OpenRouter Free (Auto)", "provider": "OpenRouter", "free": True},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "Llama 3.3 70B Instruct", "provider": "OpenRouter", "free": True},
    {"id": "z-ai/glm-4.5-air:free", "name": "GLM-4.5 Air", "provider": "OpenRouter", "free": True},
    {"id": "nvidia/nemotron-3-nano-30b-a3b:free", "name": "Nemotron 3 Nano 30B A3B", "provider": "OpenRouter", "free": True},
    {"id": "deepseek/deepseek-r1-0528:free", "name": "DeepSeek R1 0528", "provider": "OpenRouter", "free": True},
    {"id": "arcee-ai/trinity-mini:free", "name": "Trinity Mini", "provider": "OpenRouter", "free": True},
    # Premium models via OpenRouter
    {"id": "google/gemini-3-flash-preview", "name": "Gemini 3 Flash Preview", "provider": "OpenRouter"},
    {"id": "google/gemini-2.5-pro", "name": "Gemini 2.5 Pro", "provider": "OpenRouter"},
    {"id": "openai/gpt-4-turbo", "name": "GPT-4 Turbo", "provider": "OpenRouter"},
    {"id": "anthropic/claude-3.5-sonnet", "name": "Claude 3.5 Sonnet", "provider": "OpenRouter"},
    {"id": "mistralai/mistral-large-latest", "name": "Mistral Large (Latest)", "provider": "OpenRouter"},
    {"id": "mistralai/devstral-2", "name": "Devstral 2", "provider": "OpenRouter"},
    # Ollama models (local, not via OpenRouter)
    {
        "id": "qwen2.5-coder:3b",
        "name": "Qwen2.5 Coder 3B",
        "provider": "Ollama"
    },
    {
        "id": "mistral:7b-instruct",
        "name": "Mistral 7B Instruct",
        "provider": "Ollama"
    },
    {
        "id": "mistral:7b",
        "name": "Mistral 7B",
        "provider": "Ollama"
    },
    {
        "id": "llama3.1:8b",
        "name": "Llama 3.1 8B",
        "provider": "Ollama"
    },
    {
        "id": "llama3.2:3b",
        "name": "Llama 3.2 3B",
        "provider": "Ollama"
    },
    {
        "id": "qwen2.5:1.5b",
        "name": "Qwen 2.5 1.5B",
        "provider": "Ollama"
    },
    {
        "id": "deepseek-coder:1.3b",
        "name": "DeepSeek Coder 1.3B",
        "provider": "Ollama"
    }
]

DEFAULT_MODEL = "google/gemini-3-flash-preview"

# Global state for runtime model selection
_global_state = {
    "selected_model": DEFAULT_MODEL
}


def get_openrouter_api_key() -> str:
    """Get OpenRouter API key from environment or .env file."""
    return os.environ.get("OPENROUTER_API_KEY", "")


def get_selected_model() -> str:
    """Get the currently selected model."""
    return _global_state["selected_model"]


def set_selected_model(model_id: str) -> None:
    """Set the selected model."""
    _global_state["selected_model"] = model_id


def check_ollama_status() -> dict:
    """Check if Ollama is running."""
    ollama_url = Settings.get_ollama_base_url()
    try:
        with httpx.Client() as client:
            response = client.get(f"{ollama_url}/api/tags", timeout=2.0)
            if response.status_code == 200:
                return {"status": "running", "available": True}
            return {"status": "error", "available": False}
    except Exception:
        return {"status": "offline", "available": False}


class Settings(BaseSettings):
    """API settings."""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    
    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = DEFAULT_MODEL
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    
    # Model selection
    selected_model: str = DEFAULT_MODEL
    ollama_base_url: str = ""
    
    @staticmethod
    def get_ollama_base_url() -> str:
        return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    
    # MCP Server (path inside Docker container)
    mcp_server_path: str = "/mcp-server"
    
    # Temp storage
    temp_upload_dir: str = "/tmp/vivian-uploads"
    
    # Confidence threshold for human review
    confidence_threshold: float = 0.85
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    class Config:
        env_file = ".env"
        env_prefix = "VIVIAN_API_"
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Override with environment variable if set
        if "OPENROUTER_API_KEY" in os.environ:
            self.openrouter_api_key = os.environ["OPENROUTER_API_KEY"]
