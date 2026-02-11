"""Integration endpoints (Google OAuth / connection status)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import (
    CurrentUserContext,
    get_current_user_context,
    require_roles,
)
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.repositories.connection_repository import HomeConnectionRepository
from vivian_api.services.google_integration import (
    get_google_client_id,
    get_google_client_secret,
)


router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(get_current_user_context)],
)
settings = Settings()

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"
OAUTH_STATE_TTL_MINUTES = 10
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]
_oauth_state_store: dict[str, dict[str, str]] = {}


class GoogleIntegrationStatus(BaseModel):
    connected: bool
    provider_email: str | None = None
    connected_by: str | None = None
    connected_at: str | None = None
    scopes: list[str] | None = None
    message: str


class GoogleConnectionRequest(BaseModel):
    """Request to check/refresh Google connection status."""
    pass


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


def _get_default_home_id(current_user: CurrentUserContext) -> str:
    """Get the user's default home ID."""
    if not current_user.default_membership:
        raise HTTPException(status_code=400, detail="No home membership found")
    return str(current_user.default_membership.home_id)


async def _refresh_google_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> tuple[bool, dict]:
    """Attempt to refresh Google access token."""
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


async def _get_token_info(access_token: str) -> dict:
    """Get token info from Google to verify scopes and get email."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            TOKEN_INFO_URL,
            params={"access_token": access_token},
        )
    if response.status_code == 200:
        return response.json()
    return {}


@router.get("/google/status", response_model=GoogleIntegrationStatus)
async def get_google_status(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get Google Drive/Sheets connection status with live validation."""
    home_id = _get_default_home_id(current_user)
    repo = HomeConnectionRepository(db)

    connection = repo.get_by_home_and_provider(
        home_id=home_id,
        provider="google",
        connection_type="drive_sheets",
    )

    if not connection:
        return GoogleIntegrationStatus(
            connected=False,
            message="Not connected",
        )

    # Live validation: attempt token refresh
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)
    refresh_token = repo.get_decrypted_refresh_token(connection)

    if not client_id or not client_secret:
        return GoogleIntegrationStatus(
            connected=False,
            message="Server configuration error",
        )

    ok, token_data = await _refresh_google_token(
        client_id, client_secret, refresh_token
    )

    if not ok:
        # Token is invalid/revoked - delete the connection
        repo.delete(connection)
        return GoogleIntegrationStatus(
            connected=False,
            message="Connection expired or revoked. Please reconnect.",
        )

    # Token is valid - update cached access token
    access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)
    expires_at = _utc_now() + timedelta(seconds=expires_in)

    # Get token info to verify scopes and email
    token_info = await _get_token_info(access_token)
    scopes = token_info.get("scope", "").split()
    email = token_info.get("email") or connection.provider_email

    # Update connection with new access token and info
    repo.update_tokens(
        connection,
        access_token=access_token,
        token_expires_at=expires_at,
        scopes=scopes,
        provider_email=email,
    )

    return GoogleIntegrationStatus(
        connected=True,
        provider_email=email,
        connected_by=connection.connected_by_user.name if connection.connected_by_user else None,
        connected_at=connection.connected_at.isoformat() if connection.connected_at else None,
        scopes=scopes,
        message="Connected and validated",
    )


@router.get("/google/oauth/start")
async def start_google_oauth(
    return_to: str = Query(default="", description="Where to send user after OAuth"),
    current_user: CurrentUserContext = Depends(require_roles("owner", "parent")),
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
        "user_id": str(current_user.user.id),
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
    db: Session = Depends(get_db),
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
    access_token = token_payload.get("access_token")
    expires_in = token_payload.get("expires_in", 3600)
    scopes = token_payload.get("scope", "").split()

    if not refresh_token:
        return RedirectResponse(_redirect_with_status(return_to, "error", "missing_refresh_token"))

    # Get user info from the state
    user_id = oauth_state.get("user_id")
    if not user_id:
        return RedirectResponse(_redirect_with_status(return_to, "error", "missing_user_context"))

    # Get user's default home
    from sqlalchemy import select
    from vivian_api.models.identity_models import HomeMembership

    membership = db.scalar(
        select(HomeMembership)
        .where(HomeMembership.client_id == user_id)
        .where(HomeMembership.is_default_home == True)
    )
    if not membership:
        # Fall back to first membership
        membership = db.scalar(
            select(HomeMembership).where(HomeMembership.client_id == user_id)
        )
    if not membership:
        return RedirectResponse(_redirect_with_status(return_to, "error", "no_home_membership"))

    home_id = str(membership.home_id)

    # Get email from token info
    token_info = await _get_token_info(access_token)
    provider_email = token_info.get("email")

    # Save connection to database
    repo = HomeConnectionRepository(db)
    expires_at = _utc_now() + timedelta(seconds=expires_in)

    existing = repo.get_by_home_and_provider(home_id, "google", "drive_sheets")
    if existing:
        repo.update_tokens(
            existing,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expires_at=expires_at,
            scopes=scopes,
            provider_email=provider_email,
        )
    else:
        repo.create(
            home_id=home_id,
            provider="google",
            connection_type="drive_sheets",
            connected_by=user_id,
            refresh_token=refresh_token,
            access_token=access_token,
            token_expires_at=expires_at,
            scopes=scopes,
            provider_email=provider_email,
        )

    return RedirectResponse(_redirect_with_status(return_to, "connected"))


@router.post("/google/disconnect")
async def disconnect_google(
    current_user: CurrentUserContext = Depends(require_roles("owner")),
    db: Session = Depends(get_db),
):
    """Disconnect Google integration by removing stored refresh token."""
    home_id = _get_default_home_id(current_user)
    repo = HomeConnectionRepository(db)

    connection = repo.get_by_home_and_provider(
        home_id=home_id,
        provider="google",
        connection_type="drive_sheets",
    )

    if connection:
        repo.delete(connection)

    return {"success": True, "message": "Google integration disconnected"}


# Backward compatibility: also accept POST to check status (for explicit validation)
@router.post("/google/status", response_model=GoogleIntegrationStatus)
async def post_google_status(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """POST endpoint for explicit status check with validation."""
    return await get_google_status(current_user, db)
