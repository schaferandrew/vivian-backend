"""FastAPI auth dependencies and role guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from vivian_api.auth.security import TokenExpiredError, TokenInvalidError, decode_access_token
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.models.identity_models import HomeMembership, User


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
