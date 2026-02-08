"""Google OAuth integration helpers for API and MCP runtime."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from vivian_api.config import Settings


TOKEN_URL = "https://oauth2.googleapis.com/token"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_google_token_store(settings: Settings) -> dict[str, Any]:
    """Load persisted Google OAuth token data."""
    path = Path(settings.google_oauth_token_store_path)
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_google_token_store(settings: Settings, data: dict[str, Any]) -> None:
    """Persist Google OAuth token data."""
    path = Path(settings.google_oauth_token_store_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def clear_google_token_store(settings: Settings) -> None:
    """Clear persisted Google OAuth token data."""
    path = Path(settings.google_oauth_token_store_path)
    if path.exists():
        path.unlink()


def get_google_client_id(settings: Settings) -> str:
    """Resolve Google OAuth client ID."""
    return (
        settings.google_client_id
        or os.environ.get("VIVIAN_MCP_GOOGLE_CLIENT_ID", "")
        or os.environ.get("GOOGLE_CLIENT_ID", "")
    )


def get_google_client_secret(settings: Settings) -> str:
    """Resolve Google OAuth client secret."""
    return (
        settings.google_client_secret
        or os.environ.get("VIVIAN_MCP_GOOGLE_CLIENT_SECRET", "")
        or os.environ.get("GOOGLE_CLIENT_SECRET", "")
    )


def get_google_refresh_token(settings: Settings) -> str:
    """Resolve Google refresh token from store/env."""
    token_store = load_google_token_store(settings)
    return (
        token_store.get("refresh_token", "")
        or settings.google_refresh_token
        or os.environ.get("VIVIAN_MCP_GOOGLE_REFRESH_TOKEN", "")
    )


async def refresh_google_access_token(settings: Settings) -> tuple[bool, dict[str, Any]]:
    """Attempt to refresh Google access token using stored refresh token."""
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    refresh_token = get_google_refresh_token(settings)

    if not client_id or not client_secret or not refresh_token:
        return False, {"error": "Missing Google OAuth credentials"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    if response.status_code != 200:
        error_payload = response.json() if response.text else {}
        return False, {"error": "Token refresh failed", "details": error_payload}

    return True, response.json()


def apply_google_credentials_to_process_env(settings: Settings) -> None:
    """Set Google credentials in current process env for subprocess inheritance."""
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    refresh_token = get_google_refresh_token(settings)

    if client_id:
        os.environ["VIVIAN_MCP_GOOGLE_CLIENT_ID"] = client_id
    if client_secret:
        os.environ["VIVIAN_MCP_GOOGLE_CLIENT_SECRET"] = client_secret
    if refresh_token:
        os.environ["VIVIAN_MCP_GOOGLE_REFRESH_TOKEN"] = refresh_token


def build_mcp_env(settings: Settings) -> dict[str, str]:
    """Build environment for MCP subprocess with latest Google credentials."""
    env = dict(os.environ)

    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    refresh_token = get_google_refresh_token(settings)

    if client_id:
        env["VIVIAN_MCP_GOOGLE_CLIENT_ID"] = client_id
    if client_secret:
        env["VIVIAN_MCP_GOOGLE_CLIENT_SECRET"] = client_secret
    if refresh_token:
        env["VIVIAN_MCP_GOOGLE_REFRESH_TOKEN"] = refresh_token

    if settings.mcp_drive_root_folder_id:
        env["VIVIAN_MCP_DRIVE_ROOT_FOLDER_ID"] = settings.mcp_drive_root_folder_id
    if settings.mcp_reimbursed_folder_id:
        env["VIVIAN_MCP_REIMBURSED_FOLDER_ID"] = settings.mcp_reimbursed_folder_id
    if settings.mcp_unreimbursed_folder_id:
        env["VIVIAN_MCP_UNREIMBURSED_FOLDER_ID"] = settings.mcp_unreimbursed_folder_id
    if settings.mcp_not_eligible_folder_id:
        env["VIVIAN_MCP_NOT_ELIGIBLE_FOLDER_ID"] = settings.mcp_not_eligible_folder_id
    if settings.mcp_sheets_spreadsheet_id:
        env["VIVIAN_MCP_SHEETS_SPREADSHEET_ID"] = settings.mcp_sheets_spreadsheet_id

    return env


def create_google_connection_payload(settings: Settings) -> dict[str, Any]:
    """Build integration status payload used by settings UI."""
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    token_store = load_google_token_store(settings)
    refresh_token = (
        token_store.get("refresh_token")
        or settings.google_refresh_token
        or os.environ.get("VIVIAN_MCP_GOOGLE_REFRESH_TOKEN", "")
    )

    has_targets = bool(
        settings.mcp_reimbursed_folder_id
        and settings.mcp_unreimbursed_folder_id
        and settings.mcp_sheets_spreadsheet_id
    )

    return {
        "connected": bool(client_id and client_secret and refresh_token),
        "outdated": False,
        "has_required_targets": has_targets,
        "last_connected_at": token_store.get("connected_at"),
        "updated_at": _now_iso(),
    }
