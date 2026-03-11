"""Internal API endpoints for the MCP server and other internal consumers.

NOTE: These endpoints are intended for internal Docker network use only.
Infrastructure-level network policy enforces this; no additional network auth
is applied beyond the API key check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import ApiKeyContext, get_home_from_api_key
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.repositories.connection_repository import HomeConnectionRepository

router = APIRouter(tags=["internal"])

settings = Settings()


class GoogleTokenResponse(BaseModel):
    refresh_token: str
    email: str | None


@router.get("/internal/google-token", response_model=GoogleTokenResponse)
def get_google_token(
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> GoogleTokenResponse:
    """Return the Google refresh token for the API key's home.

    Used by the MCP server to obtain Google credentials without storing them
    locally.

    Returns:
        200: { refresh_token, email }
        401: invalid_api_key (handled by get_home_from_api_key)
        404: google_not_connected
    """
    repo = HomeConnectionRepository(db)
    connection = repo.get_by_home_and_provider(
        api_key_ctx.home_id,
        provider="google",
        connection_type="drive_sheets",
    )

    if not connection:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "google_not_connected",
                "message": "Google account not connected for this home.",
                "action": "Ask your admin to connect Google in Settings.",
                "settings_url": f"{settings.vivian_url}/settings/connections",
            },
        )

    refresh_token = repo.get_decrypted_refresh_token(connection)
    return GoogleTokenResponse(
        refresh_token=refresh_token,
        email=connection.provider_email,
    )
