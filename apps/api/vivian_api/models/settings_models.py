"""SQLAlchemy ORM models for home settings, link settings, and external connections."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import ARRAY, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from vivian_api.db.database import Base


class McpServerSettings(Base):
    """Per-home configuration for an MCP server (e.g. hsa_ledger, charitable_ledger)."""

    __tablename__ = "mcp_server_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    home_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    mcp_server_id: Mapped[str] = mapped_column(String(255), nullable=False)
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    home: Mapped["Home"] = relationship(back_populates="mcp_server_settings")

class HomeLinkSetting(Base):
    """A named URL/port link associated with a home (e.g. Jellyfin, Mealie)."""

    __tablename__ = "home_link_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    home_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    icon: Mapped[str | None] = mapped_column(String(100), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    home: Mapped["Home"] = relationship(back_populates="link_settings")

class HomeConnection(Base):
    """An OAuth connection from a home to an external provider (e.g. Google)."""

    __tablename__ = "home_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    home_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("homes.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    connection_type: Mapped[str] = mapped_column(String(100), nullable=False)
    connected_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scopes: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    provider_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    home: Mapped["Home"] = relationship(back_populates="connections")