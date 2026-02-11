"""Vivian API configuration."""

import os
from pathlib import Path
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
    {"id": "mistralai/mistral-large-2512", "name": "Mistral Large (Latest)", "provider": "OpenRouter"},
    {"id": "mistralai/devstral-2512", "name": "Devstral 2", "provider": "OpenRouter"},
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
    "selected_model": DEFAULT_MODEL,
    "enabled_mcp_servers": [],
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


def get_enabled_mcp_servers() -> list[str]:
    """Get currently enabled MCP server IDs."""
    enabled = _global_state.get("enabled_mcp_servers")
    if isinstance(enabled, list) and enabled:
        return list(dict.fromkeys(str(v) for v in enabled if str(v).strip()))

    defaults = Settings().mcp_default_enabled_servers
    parsed = [part.strip() for part in defaults.split(",") if part.strip()]
    if parsed:
        _global_state["enabled_mcp_servers"] = parsed
    return parsed


def set_enabled_mcp_servers(server_ids: list[str]) -> None:
    """Set enabled MCP server IDs globally."""
    _global_state["enabled_mcp_servers"] = list(
        dict.fromkeys(str(server_id) for server_id in server_ids if str(server_id).strip())
    )


def get_ollama_base_url() -> str:
    """Get Ollama base URL."""
    return os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


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
    
    # Database URL (for SQLAlchemy)
    database_url: str = ""

    # Authentication
    auth_jwt_secret: str = "dev-change-me"
    auth_jwt_algorithm: str = "HS256"
    auth_access_token_minutes: int = 15
    auth_refresh_token_days: int = 30
    
    # MCP server package path used by receipt workflows.
    mcp_server_path: str = "/mcp-server"
    # MCP servers root path for registry-discovered servers.
    mcp_servers_root_path: str = "/mcp-servers"
    mcp_default_enabled_servers: str = "vivian_receipt_tool"
    # JSON array for future custom server definitions.
    mcp_custom_servers_json: str = ""
    user_location: str = ""
    
    # Temp storage
    temp_upload_dir: str = "/tmp/vivian-uploads"
    
    # Confidence threshold for human review
    confidence_threshold: float = 0.85
    
    # CORS
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    # Google OAuth settings
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/integrations/google/oauth/callback"
    google_oauth_success_redirect: str = "http://localhost:3000/settings?google=connected"
    google_oauth_error_redirect: str = "http://localhost:3000/settings?google=error"
    google_oauth_token_store_path: str = "/tmp/vivian-uploads/google-oauth.json"
    
    # MCP Google Drive/Sheets settings
    mcp_drive_root_folder_id: str = ""
    mcp_sheets_spreadsheet_id: str = ""
    mcp_reimbursed_folder_id: str = ""
    mcp_unreimbursed_folder_id: str = ""
    
    # Charitable donation settings
    charitable_drive_folder_id: str = ""
    charitable_spreadsheet_id: str = ""
    charitable_worksheet_name: str = "Charitable_Ledger"
    
    class Config:
        env_file = ".env"
        env_prefix = "VIVIAN_API_"
        extra = "ignore"  # Allow extra env vars for flexibility
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Override with environment variable if set
        if "OPENROUTER_API_KEY" in os.environ:
            self.openrouter_api_key = os.environ["OPENROUTER_API_KEY"]
        # Also check for non-prefixed DATABASE_URL
        if "DATABASE_URL" in os.environ and not self.database_url:
            self.database_url = os.environ["DATABASE_URL"]

        # Backward-compatible fallback: accept MCP-prefixed vars directly.
        fallback_env = {
            "mcp_drive_root_folder_id": "VIVIAN_MCP_DRIVE_ROOT_FOLDER_ID",
            "mcp_reimbursed_folder_id": "VIVIAN_MCP_REIMBURSED_FOLDER_ID",
            "mcp_unreimbursed_folder_id": "VIVIAN_MCP_UNREIMBURSED_FOLDER_ID",
            "mcp_sheets_spreadsheet_id": "VIVIAN_MCP_SHEETS_SPREADSHEET_ID",
            "charitable_drive_folder_id": "VIVIAN_MCP_CHARITABLE_DRIVE_FOLDER_ID",
            "charitable_spreadsheet_id": "VIVIAN_MCP_CHARITABLE_SPREADSHEET_ID",
            "charitable_worksheet_name": "VIVIAN_MCP_CHARITABLE_WORKSHEET_NAME",
        }
        for field_name, env_name in fallback_env.items():
            if not getattr(self, field_name):
                value = os.environ.get(env_name, "")
                if value:
                    setattr(self, field_name, value)

    def resolve_mcp_server_path(self, folder_name: str) -> str:
        """Resolve MCP server directory by folder name under root path."""
        return str(Path(self.mcp_servers_root_path) / folder_name)


def check_ollama_status() -> dict:
    """Check if Ollama is running."""
    ollama_url = get_ollama_base_url()
    try:
        with httpx.Client() as client:
            response = client.get(f"{ollama_url}/api/tags", timeout=2.0)
            if response.status_code == 200:
                return {"status": "running", "available": True}
            return {"status": "error", "available": False}
    except Exception:
        return {"status": "offline", "available": False}


settings = Settings()
