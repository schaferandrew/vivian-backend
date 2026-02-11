"""Typed SQLAlchemy ORM models for client/home identity persistence."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    UUID,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vivian_api.db.database import Base


CLIENT_STATUSES = ("active", "disabled", "invited")
MEMBERSHIP_ROLES = ("owner", "parent", "child", "caretaker", "guest", "member")


class Home(Base):
    """Household container shared by one or more users."""

    __tablename__ = "homes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    memberships: Mapped[list["HomeMembership"]] = relationship(
        back_populates="home",
        cascade="all, delete-orphan",
    )
    connections: Mapped[list["HomeConnection"]] = relationship(
        back_populates="home",
        cascade="all, delete-orphan",
    )
    mcp_settings: Mapped[list["McpServerSettings"]] = relationship(
        back_populates="home",
        cascade="all, delete-orphan",
    )


class User(Base):
    """Application user/account identity."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'invited')",
            name="ck_users_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    memberships: Mapped[list["HomeMembership"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
    )
    auth_sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class HomeMembership(Base):
    """User-to-home membership with role metadata."""

    __tablename__ = "home_memberships"
    __table_args__ = (
        UniqueConstraint("home_id", "client_id", name="uq_home_memberships_home_client"),
        CheckConstraint(
            "role IN ('owner', 'parent', 'child', 'caretaker', 'guest', 'member')",
            name="ck_home_memberships_role",
        ),
        Index(
            "uq_home_memberships_default_home_per_client",
            "client_id",
            unique=True,
            postgresql_where=text("is_default_home"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    home_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("homes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    is_default_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    home: Mapped[Home] = relationship(back_populates="memberships")
    client: Mapped[User] = relationship(back_populates="memberships")


# Backward-compatible alias while references migrate from Client -> User.
Client = User


class AuthSession(Base):
    """Refresh-token backed auth session."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint(
            "refresh_token_hash",
            name="uq_auth_sessions_refresh_token_hash",
        ),
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
        Index(
            "ix_auth_sessions_user_id_created_at",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="auth_sessions")
