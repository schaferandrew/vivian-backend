"""Authentication endpoints: login, refresh, logout, and identity."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from vivian_api.auth.dependencies import CurrentUserContext, get_current_user_context
from vivian_api.auth.schemas import (
    AuthMeResponse,
    AuthMembershipResponse,
    AuthTokenPairResponse,
    AuthUserResponse,
    HomeSettingsMemberResponse,
    HomeSettingsResponse,
    LoginRequest,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    UpdateHomeMemberRoleRequest,
    UpdateHomeSettingsRequest,
    UpdateMeRequest,
)
from vivian_api.auth.security import (
    authenticate_user,
    build_auth_session,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.models.identity_models import AuthSession, HomeMembership, MEMBERSHIP_ROLES, User


router = APIRouter(prefix="/auth", tags=["auth"])
settings = Settings()


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _client_ip(request: Request) -> str | None:
    if not request.client:
        return None
    return request.client.host


def _build_me_payload(current_user: CurrentUserContext) -> AuthMeResponse:
    memberships = [
        AuthMembershipResponse(
            id=membership.id,
            home_id=membership.home_id,
            home_name=membership.home.name,
            role=membership.role,
            is_default_home=membership.is_default_home,
        )
        for membership in current_user.memberships
    ]

    default_home = None
    if current_user.default_membership:
        membership = current_user.default_membership
        default_home = AuthMembershipResponse(
            id=membership.id,
            home_id=membership.home_id,
            home_name=membership.home.name,
            role=membership.role,
            is_default_home=membership.is_default_home,
        )

    return AuthMeResponse(
        user=AuthUserResponse(
            id=current_user.user.id,
            name=current_user.user.name,
            email=current_user.user.email,
            status=current_user.user.status,
            last_login_at=current_user.user.last_login_at,
        ),
        default_home=default_home,
        memberships=memberships,
    )


def _require_owner_membership(current_user: CurrentUserContext) -> HomeMembership:
    default_membership = current_user.default_membership
    if default_membership and default_membership.role == "owner":
        return default_membership

    fallback_membership = next(
        (membership for membership in current_user.memberships if membership.role == "owner"),
        None,
    )
    if fallback_membership:
        return fallback_membership

    raise HTTPException(status_code=403, detail="Owner role is required.")


def _build_home_settings_payload(
    *,
    home_membership: HomeMembership,
    members: list[HomeMembership],
) -> HomeSettingsResponse:
    return HomeSettingsResponse(
        home_id=home_membership.home_id,
        home_name=home_membership.home.name,
        timezone=home_membership.home.timezone,
        members=[
            HomeSettingsMemberResponse(
                membership_id=member.id,
                user_id=member.client_id,
                name=member.client.name,
                email=member.client.email,
                status=member.client.status,
                role=member.role,
                is_default_home=member.is_default_home,
            )
            for member in members
        ],
    )


def _load_home_memberships(db: Session, home_id: str) -> list[HomeMembership]:
    return list(
        db.scalars(
            select(HomeMembership)
            .options(selectinload(HomeMembership.client))
            .where(HomeMembership.home_id == home_id)
            .order_by(HomeMembership.created_at.asc())
        )
    )


@router.post("/login", response_model=AuthTokenPairResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token, expires_in = create_access_token(user=user, settings=settings)
    refresh_token = generate_refresh_token()

    session = build_auth_session(
        user_id=user.id,
        refresh_token=refresh_token,
        settings=settings,
        user_agent=request.headers.get("user-agent"),
        ip_address=_client_ip(request),
    )

    user.last_login_at = _utc_now_naive()
    db.add(session)
    db.commit()

    return AuthTokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/refresh", response_model=AuthTokenPairResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    refresh_token_hash = hash_refresh_token(payload.refresh_token)
    current_session = db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == refresh_token_hash)
    )
    if not current_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    now = _utc_now_naive()
    if current_session.revoked_at is not None or current_session.expires_at <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    current_session.revoked_at = now

    refresh_token = generate_refresh_token()
    new_session = build_auth_session(
        user_id=current_session.user_id,
        refresh_token=refresh_token,
        settings=settings,
        user_agent=request.headers.get("user-agent") or current_session.user_agent,
        ip_address=_client_ip(request) or current_session.ip_address,
    )
    db.add(new_session)

    access_token, expires_in = create_access_token(user=current_session.user, settings=settings)
    db.commit()

    return AuthTokenPairResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post("/logout", response_model=LogoutResponse)
def logout(
    payload: LogoutRequest,
    db: Session = Depends(get_db),
):
    refresh_token_hash = hash_refresh_token(payload.refresh_token)
    session = db.scalar(
        select(AuthSession).where(AuthSession.refresh_token_hash == refresh_token_hash)
    )
    if session and session.revoked_at is None:
        session.revoked_at = _utc_now_naive()
        db.commit()

    return LogoutResponse(success=True)


@router.get("/me", response_model=AuthMeResponse)
def me(
    current_user: CurrentUserContext = Depends(get_current_user_context),
):
    return _build_me_payload(current_user)


@router.get("/home-settings", response_model=HomeSettingsResponse)
def get_home_settings(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    owner_membership = _require_owner_membership(current_user)
    members = _load_home_memberships(db, owner_membership.home_id)
    return _build_home_settings_payload(
        home_membership=owner_membership,
        members=members,
    )


@router.patch("/home-settings", response_model=HomeSettingsResponse)
def update_home_settings(
    payload: UpdateHomeSettingsRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    owner_membership = _require_owner_membership(current_user)
    cleaned_home_name = payload.home_name.strip()
    if not cleaned_home_name:
        raise HTTPException(status_code=400, detail="Home name cannot be empty.")

    home = owner_membership.home
    home.name = cleaned_home_name
    db.commit()
    db.refresh(home)

    members = _load_home_memberships(db, owner_membership.home_id)
    db.refresh(owner_membership)
    return _build_home_settings_payload(
        home_membership=owner_membership,
        members=members,
    )


@router.patch("/home-settings/members/{membership_id}", response_model=HomeSettingsMemberResponse)
def update_home_member_role(
    membership_id: str,
    payload: UpdateHomeMemberRoleRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    owner_membership = _require_owner_membership(current_user)
    next_role = payload.role.strip().lower()
    if next_role not in MEMBERSHIP_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role.")

    target = db.scalar(
        select(HomeMembership)
        .options(selectinload(HomeMembership.client))
        .where(HomeMembership.id == membership_id)
    )
    if target is None or target.home_id != owner_membership.home_id:
        raise HTTPException(status_code=404, detail="Home member not found.")

    if target.role == "owner" and next_role != "owner":
        owner_count = db.scalar(
            select(func.count())
            .select_from(HomeMembership)
            .where(
                HomeMembership.home_id == target.home_id,
                HomeMembership.role == "owner",
            )
        )
        if int(owner_count or 0) <= 1:
            raise HTTPException(status_code=400, detail="A home must have at least one owner.")

    target.role = next_role
    db.commit()
    db.refresh(target)

    return HomeSettingsMemberResponse(
        membership_id=target.id,
        user_id=target.client_id,
        name=target.client.name,
        email=target.client.email,
        status=target.client.status,
        role=target.role,
        is_default_home=target.is_default_home,
    )


@router.patch("/me", response_model=AuthMeResponse)
def update_me(
    payload: UpdateMeRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    user = current_user.user
    updated = False

    if payload.name is not None:
        cleaned_name = payload.name.strip()
        user.name = cleaned_name or None
        updated = True

    if payload.email is not None:
        cleaned_email = payload.email.strip().lower()
        if "@" not in cleaned_email:
            raise HTTPException(status_code=400, detail="Invalid email format")
        existing = db.scalar(select(User).where(User.email == cleaned_email))
        if existing and existing.id != user.id:
            raise HTTPException(status_code=409, detail="Email already in use")
        user.email = cleaned_email
        updated = True

    if payload.password is not None:
        new_password = payload.password or ""
        if new_password:
            if user.password_hash:
                if not payload.current_password:
                    raise HTTPException(status_code=400, detail="Current password required")
                if not verify_password(payload.current_password, user.password_hash):
                    raise HTTPException(status_code=401, detail="Current password is incorrect")
            user.password_hash = hash_password(new_password)
            updated = True

    if updated:
        db.commit()
        db.refresh(user)

    return _build_me_payload(
        CurrentUserContext(
            user=user,
            memberships=current_user.memberships,
            default_membership=current_user.default_membership,
        )
    )
