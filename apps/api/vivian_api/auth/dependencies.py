"""FastAPI auth dependencies and role guards."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vivian_api.auth.security import TokenExpiredError, TokenInvalidError, decode_access_token
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.models.identity_models import HomeApiKey, HomeMembership, User


@dataclass(slots=True)
class CurrentUserContext:
    user: User
    memberships: list[HomeMembership]
    default_membership: HomeMembership | None


settings = Settings()


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise _unauthorized()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("Invalid authorization header")
    return token.strip()


def get_current_user_context(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
) -> CurrentUserContext:
    token = _extract_bearer_token(authorization)

    try:
        payload = decode_access_token(token, settings)
    except TokenExpiredError as exc:
        raise _unauthorized("Access token expired") from exc
    except TokenInvalidError as exc:
        raise _unauthorized("Invalid access token") from exc

    user_id = str(payload.get("sub") or "").strip()
    token_type = str(payload.get("type") or "")
    if not user_id or token_type != "access":
        raise _unauthorized("Invalid access token payload")

    user = db.scalar(
        select(User)
        .options(
            selectinload(User.memberships).selectinload(HomeMembership.home),
        )
        .where(User.id == user_id)
    )
    if not user:
        raise _unauthorized("User not found")

    memberships = list(user.memberships)
    default_membership = next(
        (membership for membership in memberships if membership.is_default_home),
        memberships[0] if memberships else None,
    )

    return CurrentUserContext(
        user=user,
        memberships=memberships,
        default_membership=default_membership,
    )


@dataclass(slots=True)
class ApiKeyContext:
    home_id: str
    key_record: HomeApiKey


def get_home_from_api_key(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    db: Session = Depends(get_db),
) -> ApiKeyContext:
    """Validate a home API key (viv_sk_...) and return its home context.

    Updates last_used_at on every successful validation.
    """
    from vivian_api.repositories.api_key_repository import ApiKeyRepository

    token = _extract_bearer_token(authorization)
    if not token.startswith("viv_sk_"):
        raise _unauthorized("Invalid API key format")

    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    repo = ApiKeyRepository(db)
    key_record = repo.get_by_hash(key_hash)
    if not key_record:
        raise HTTPException(status_code=401, detail="invalid_api_key")

    repo.update_last_used(key_record)
    return ApiKeyContext(home_id=str(key_record.home_id), key_record=key_record)


def require_roles(*roles: str):
    allowed_roles = set(roles)

    def _dependency(
        current_user: CurrentUserContext = Depends(get_current_user_context),
    ) -> CurrentUserContext:
        if not current_user.memberships:
            raise HTTPException(status_code=403, detail="No home memberships")

        if not any(membership.role in allowed_roles for membership in current_user.memberships):
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of roles: {', '.join(sorted(allowed_roles))}",
            )

        return current_user

    return _dependency
