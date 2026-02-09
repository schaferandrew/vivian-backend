"""MCP server configuration."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """MCP server settings."""
    
    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    
    # Google Drive/Sheets
    drive_root_folder_id: str = ""
    sheets_spreadsheet_id: str = ""
    sheets_worksheet_name: str = "HSA_Ledger"
    
    # Folders within root
    reimbursed_folder_id: str = ""
    unreimbursed_folder_id: str = ""
    not_eligible_folder_id: str = ""
    
    class Config:
        env_file = ".env"
        env_prefix = "VIVIAN_MCP_"
