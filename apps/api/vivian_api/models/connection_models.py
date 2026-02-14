"""Typed SQLAlchemy ORM models for connections and MCP settings persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vivian_api.db.database import Base


class HomeConnection(Base):
    """Service-level connection (e.g., Google Drive/Sheets) for a home.
    
    Unlike user_connections (for identity/auth), this is shared across the household.
    The connected_by field tracks who set it up for audit purposes.
    """

    __tablename__ = "home_connections"
    __table_args__ = (
        Index(
            "ix_home_connections_home_id_provider_type",
            "home_id",
            "provider",
            "connection_type",
            unique=True,
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
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    connection_type: Mapped[str] = mapped_column(String(50), nullable=False)
    connected_by: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id"),
        nullable=False,
    )
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scopes: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text).with_variant(JSON, "sqlite"),
        nullable=True,
    )
    provider_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    home: Mapped["Home"] = relationship("Home", back_populates="connections")
    connected_by_user: Mapped["User"] = relationship("User")


class McpServerSettings(Base):
    """Per-home, per-MCP-server configurable settings.
    
    The settings_json field is flexible JSON that can hold any key-value pairs
    defined by the MCP server's settings_schema. This allows new MCP servers
    to define custom settings without requiring database migrations.
    """

    __tablename__ = "mcp_server_settings"
    __table_args__ = (
        Index(
            "ix_mcp_server_settings_home_id_server",
            "home_id",
            "mcp_server_id",
            unique=True,
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
    mcp_server_id: Mapped[str] = mapped_column(String(100), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    home: Mapped["Home"] = relationship("Home", back_populates="mcp_settings")
