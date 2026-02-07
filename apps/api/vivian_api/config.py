"""Vivian API configuration."""

import os
from typing import Optional
import httpx
from pydantic_settings import BaseSettings


AVAILABLE_MODELS = [
    {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo", "provider": "OpenAI"},
    {"id": "gpt-4", "name": "GPT-4", "provider": "OpenAI"},
    {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "provider": "Anthropic"},
    {"id": "google/gemini-flash-1.5", "name": "Gemini Flash 1.5", "provider": "Google"},
    {"id": "ollama/llama3.1:8b", "name": "Llama 3.1 8B", "provider": "Ollama"},
    {"id": "ollama/llama3.2:3b", "name": "Llama 3.2 3B", "provider": "Ollama"},
    {"id": "ollama/mistral:7b-instruct", "name": "Mistral 7B Instruct", "provider": "Ollama"},
]

DEFAULT_MODEL = "gpt-3.5-turbo"

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
