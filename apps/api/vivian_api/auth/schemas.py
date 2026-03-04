"""Pydantic schemas for auth endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str = Field(default="", max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=16)


class AuthTokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthUserResponse(BaseModel):
    id: str
    name: str | None
    email: str
    status: str
    last_login_at: datetime | None


class AuthMembershipResponse(BaseModel):
    id: str
    home_id: str
    home_name: str
    role: str
    is_default_home: bool


class AuthMeResponse(BaseModel):
    user: AuthUserResponse
    default_home: AuthMembershipResponse | None
    memberships: list[AuthMembershipResponse]


class HomeSettingsMemberResponse(BaseModel):
    membership_id: str
    user_id: str
    name: str | None
    email: str
    status: str
    role: str
    is_default_home: bool


class HomeSettingsResponse(BaseModel):
    home_id: str
    home_name: str
    timezone: str
    members: list[HomeSettingsMemberResponse]


class UpdateHomeSettingsRequest(BaseModel):
    home_name: str = Field(min_length=1, max_length=255)


class UpdateHomeMemberRoleRequest(BaseModel):
    role: str = Field(min_length=1, max_length=32)


class UpdateMeRequest(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    password: str | None = Field(default=None, max_length=512)
    current_password: str | None = Field(default=None, max_length=512)


class LogoutResponse(BaseModel):
    success: bool
