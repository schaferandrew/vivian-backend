"""MCP server configuration."""

import json
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MCP server settings."""
    
    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    google_oauth_token_store_path: str = "/tmp/vivian-uploads/google-oauth.json"
    
    # Google Drive/Sheets (HSA)
    drive_root_folder_id: str = ""
    hsa_spreadsheet_id: str = ""
    hsa_worksheet_name: str = ""
    
    # Google Drive/Sheets (Charitable)
    charitable_spreadsheet_id: str = ""
    charitable_worksheet_name: str = ""
    charitable_drive_folder_id: str = ""
    
    # Folders within root (HSA)
    reimbursed_folder_id: str = ""
    unreimbursed_folder_id: str = ""
    not_eligible_folder_id: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VIVIAN_MCP_",
        extra="ignore",
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Backward-compatible fallback for deployments that only define API-prefixed vars.
        fallback_env = {
            "google_client_id": "VIVIAN_API_GOOGLE_CLIENT_ID",
            "google_client_secret": "VIVIAN_API_GOOGLE_CLIENT_SECRET",
            "google_refresh_token": "VIVIAN_API_GOOGLE_REFRESH_TOKEN",
            "google_oauth_token_store_path": "VIVIAN_API_GOOGLE_OAUTH_TOKEN_STORE_PATH",
            "drive_root_folder_id": "VIVIAN_API_MCP_DRIVE_ROOT_FOLDER_ID",
            "hsa_spreadsheet_id": "VIVIAN_API_MCP_SHEETS_SPREADSHEET_ID",
            "hsa_worksheet_name": "VIVIAN_API_MCP_SHEETS_WORKSHEET_NAME",
            "reimbursed_folder_id": "VIVIAN_API_MCP_REIMBURSED_FOLDER_ID",
            "unreimbursed_folder_id": "VIVIAN_API_MCP_UNREIMBURSED_FOLDER_ID",
            "not_eligible_folder_id": "VIVIAN_API_MCP_NOT_ELIGIBLE_FOLDER_ID",
        }
        for field_name, env_name in fallback_env.items():
            if not getattr(self, field_name):
                value = os.environ.get(env_name, "")
                if value:
                    setattr(self, field_name, value)

        # If refresh token is not configured as env, try reading the API OAuth token store.
        if not self.google_refresh_token and self.google_oauth_token_store_path:
            token_path = Path(self.google_oauth_token_store_path)
            if token_path.exists():
                try:
                    data = json.loads(token_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        self.google_refresh_token = str(data.get("refresh_token") or "")
                        if not self.google_client_id:
                            self.google_client_id = str(data.get("client_id") or "")
                        if not self.google_client_secret:
                            self.google_client_secret = str(data.get("client_secret") or "")
                except Exception:
                    # Keep env-driven behavior if token file is malformed/unreadable.
                    pass
