"""Router for per-home link/URL settings."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import CurrentUserContext, get_current_user_context
from vivian_api.db.database import get_db
from vivian_api.models.connection_models import HomeLinkSetting
from vivian_api.models.schemas import (
    HomeLinkSettingCreate,
    HomeLinkSettingResponse,
    HomeLinkSettingUpdate,
)


router = APIRouter(
    prefix="/link-settings",
    tags=["link-settings"],
    dependencies=[Depends(get_current_user_context)],
)


def _get_home_id(ctx: CurrentUserContext) -> str:
    if not ctx.default_membership:
        raise HTTPException(status_code=403, detail="No home membership found")
    return ctx.default_membership.home_id


def _to_response(setting: HomeLinkSetting) -> HomeLinkSettingResponse:
    return HomeLinkSettingResponse(
        id=setting.id,
        home_id=setting.home_id,
        key=setting.key,
        label=setting.label,
        url=setting.url,
        port=setting.port,
        icon=setting.icon,
        created_at=setting.created_at.isoformat(),
        updated_at=setting.updated_at.isoformat(),
    )


@router.get("", response_model=list[HomeLinkSettingResponse])
def list_link_settings(
    ctx: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> list[HomeLinkSettingResponse]:
    """List all link settings for the current user's home."""
    home_id = _get_home_id(ctx)
    rows = db.scalars(
        select(HomeLinkSetting)
        .where(HomeLinkSetting.home_id == home_id)
        .order_by(HomeLinkSetting.key)
    ).all()
    return [_to_response(r) for r in rows]


@router.post("", response_model=HomeLinkSettingResponse, status_code=status.HTTP_201_CREATED)
def create_link_setting(
    body: HomeLinkSettingCreate,
    ctx: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> HomeLinkSettingResponse:
    """Create a new link setting for the current user's home."""
    home_id = _get_home_id(ctx)

    existing = db.scalar(
        select(HomeLinkSetting).where(
            HomeLinkSetting.home_id == home_id,
            HomeLinkSetting.key == body.key,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A link setting with key '{body.key}' already exists",
        )

    setting = HomeLinkSetting(
        home_id=home_id,
        key=body.key,
        label=body.label,
        url=body.url,
        port=body.port,
        icon=body.icon,
    )
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return _to_response(setting)


@router.put("/{key}", response_model=HomeLinkSettingResponse)
def update_link_setting(
    key: str,
    body: HomeLinkSettingUpdate,
    ctx: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> HomeLinkSettingResponse:
    """Update an existing link setting by key."""
    home_id = _get_home_id(ctx)

    setting = db.scalar(
        select(HomeLinkSetting).where(
            HomeLinkSetting.home_id == home_id,
            HomeLinkSetting.key == key,
        )
    )
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link setting not found")

    if body.label is not None:
        setting.label = body.label
    if body.url is not None:
        setting.url = body.url
    if body.port is not None:
        setting.port = body.port
    if body.icon is not None:
        setting.icon = body.icon

    db.commit()
    db.refresh(setting)
    return _to_response(setting)


@router.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link_setting(
    key: str,
    ctx: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
) -> None:
    """Delete a link setting by key."""
    home_id = _get_home_id(ctx)

    setting = db.scalar(
        select(HomeLinkSetting).where(
            HomeLinkSetting.home_id == home_id,
            HomeLinkSetting.key == key,
        )
    )
    if not setting:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link setting not found")

    db.delete(setting)
    db.commit()
