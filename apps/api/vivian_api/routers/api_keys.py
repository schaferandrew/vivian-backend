"""API key management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import CurrentUserContext, get_current_user_context, require_roles
from vivian_api.auth.security import generate_api_key
from vivian_api.db.database import get_db
from vivian_api.repositories.api_key_repository import ApiKeyRepository

router = APIRouter(tags=["api-keys"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ApiKeyCreateRequest(BaseModel):
    name: str


class ApiKeyCreatedResponse(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    key: str  # Full key — only returned on creation


class ApiKeyListItem(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_default_home_id(ctx: CurrentUserContext) -> str:
    membership = ctx.default_membership
    if not membership:
        raise HTTPException(status_code=400, detail="No default home found")
    return str(membership.home_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/home/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_api_key(
    body: ApiKeyCreateRequest,
    ctx: CurrentUserContext = Depends(require_roles("owner")),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedResponse:
    """Create a new named API key for the home. Returns full key once."""
    home_id = _get_default_home_id(ctx)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Key name cannot be empty")

    full_key, key_hash, prefix = generate_api_key()
    repo = ApiKeyRepository(db)
    key_record = repo.create(
        home_id=home_id,
        name=name,
        key_hash=key_hash,
        key_prefix=prefix,
        created_by=ctx.user.id,
    )

    return ApiKeyCreatedResponse(
        id=key_record.id,
        name=key_record.name,
        prefix=key_record.key_prefix,
        created_at=key_record.created_at,
        key=full_key,
    )


@router.get("/home/api-keys", response_model=list[ApiKeyListItem])
def list_api_keys(
    ctx: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> list[ApiKeyListItem]:
    """List all API keys for the home (no plaintext key or hash exposed)."""
    home_id = _get_default_home_id(ctx)
    repo = ApiKeyRepository(db)
    keys = repo.list_by_home(home_id)
    return [
        ApiKeyListItem(
            id=k.id,
            name=k.name,
            prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.delete(
    "/home/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_api_key(
    key_id: str,
    ctx: CurrentUserContext = Depends(require_roles("owner")),
    db: Session = Depends(get_db),
) -> None:
    """Revoke an API key."""
    home_id = _get_default_home_id(ctx)
    repo = ApiKeyRepository(db)
    key_record = repo.get_by_id(key_id, home_id)
    if not key_record:
        raise HTTPException(status_code=404, detail="API key not found")
    repo.delete(key_record)
