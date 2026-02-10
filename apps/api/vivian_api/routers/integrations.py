"""Integration endpoints (Google OAuth / connection status)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.config import Settings
from vivian_api.services.google_integration import (
    apply_google_credentials_to_process_env,
    clear_google_token_store,
    create_google_connection_payload,
    get_google_client_id,
    get_google_client_secret,
    refresh_google_access_token,
    save_google_token_store,
)


router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(get_current_user_context)],
)
settings = Settings()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH_STATE_TTL_MINUTES = 10
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
_oauth_state_store: dict[str, dict[str, str]] = {}


class GoogleIntegrationStatus(BaseModel):
    connected: bool
    outdated: bool
    has_required_targets: bool
    last_connected_at: str | None = None
    updated_at: str
    message: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_expired_oauth_states() -> None:
    cutoff = _utc_now() - timedelta(minutes=OAUTH_STATE_TTL_MINUTES)
    expired = [
        state
        for state, payload in _oauth_state_store.items()
        if datetime.fromisoformat(payload["created_at"]) < cutoff
    ]
    for state in expired:
        _oauth_state_store.pop(state, None)


def _state_redirect(state: str, fallback: str) -> str:
    payload = _oauth_state_store.get(state)
    if not payload:
        return fallback
    return payload.get("return_to") or fallback


def _redirect_with_status(base_url: str, status: str, message: str | None = None) -> str:
    query = {"google": status}
    if message:
        query["message"] = message
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}{urlencode(query)}"


@router.get("/google/status", response_model=GoogleIntegrationStatus)
async def get_google_status():
    """Get Google Drive/Sheets connection status."""
    payload = create_google_connection_payload(settings)

    if not payload["connected"]:
        return GoogleIntegrationStatus(
            **payload,
            message="Not connected",
        )

    ok, token_data = await refresh_google_access_token(settings)
    if not ok:
        status_payload = dict(payload)
        status_payload["connected"] = False
        status_payload["outdated"] = True
        return GoogleIntegrationStatus(**status_payload, message="Connection needs to be refreshed")

    _ = token_data.get("access_token")
    message = (
        "Connected, but folder/sheet IDs are incomplete"
        if not payload["has_required_targets"]
        else "Connected"
    )
    status_payload = dict(payload)
    status_payload["connected"] = True
    status_payload["outdated"] = False
    return GoogleIntegrationStatus(**status_payload, message=message)


@router.get("/google/oauth/start")
async def start_google_oauth(
    return_to: str = Query(default="", description="Where to send user after OAuth"),
):
    """Start Google OAuth flow and redirect to consent screen."""
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail=(
                "Google OAuth client credentials are missing. Set "
                "VIVIAN_API_GOOGLE_CLIENT_ID and VIVIAN_API_GOOGLE_CLIENT_SECRET."
            ),
        )

    _cleanup_expired_oauth_states()
    state = secrets.token_urlsafe(24)
    _oauth_state_store[state] = {
        "created_at": _utc_now().isoformat(),
        "return_to": return_to or settings.google_oauth_success_redirect,
    }

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
    )

    return RedirectResponse(url=f"{AUTH_URL}?{query}")


@router.get("/google/oauth/callback")
async def google_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    """Handle Google OAuth callback, exchange code, and persist refresh token."""
    fallback = settings.google_oauth_error_redirect

    if not state:
        return RedirectResponse(_redirect_with_status(fallback, "error", "missing_state"))

    return_to = _state_redirect(state, fallback)
    oauth_state = _oauth_state_store.pop(state, None)
    if not oauth_state:
        return RedirectResponse(_redirect_with_status(return_to, "error", "invalid_state"))

    if error:
        return RedirectResponse(_redirect_with_status(return_to, "error", error))

    if not code:
        return RedirectResponse(_redirect_with_status(return_to, "error", "missing_code"))

    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    if not client_id or not client_secret:
        return RedirectResponse(_redirect_with_status(return_to, "error", "missing_client_config"))

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            token_response = await client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": settings.google_oauth_redirect_uri,
                    "grant_type": "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception:
        return RedirectResponse(_redirect_with_status(return_to, "error", "token_exchange_failed"))

    if token_response.status_code != 200:
        return RedirectResponse(_redirect_with_status(return_to, "error", "token_exchange_failed"))

    token_payload = token_response.json()
    refresh_token = token_payload.get("refresh_token")
    if not refresh_token:
        return RedirectResponse(_redirect_with_status(return_to, "error", "missing_refresh_token"))

    save_google_token_store(
        settings,
        {
            "refresh_token": refresh_token,
            "connected_at": _utc_now().isoformat(),
        },
    )
    apply_google_credentials_to_process_env(settings)

    return RedirectResponse(_redirect_with_status(return_to, "connected"))


@router.post("/google/disconnect")
async def disconnect_google():
    """Disconnect Google integration by removing stored refresh token."""
    clear_google_token_store(settings)
    return {"success": True}
