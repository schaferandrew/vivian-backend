"""Repositories for home connections and MCP settings persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from vivian_api.models.connection_models import HomeConnection, McpServerSettings
from vivian_api.services.encryption import encryption_service


class HomeConnectionRepository:
    """Repository for HomeConnection entities."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_home_and_provider(
        self,
        home_id: str,
        provider: str,
        connection_type: str,
    ) -> HomeConnection | None:
        """Get a connection by home, provider, and type."""
        stmt = select(HomeConnection).where(
            HomeConnection.home_id == home_id,
            HomeConnection.provider == provider,
            HomeConnection.connection_type == connection_type,
        )
        return self.db.scalar(stmt)

    def create(
        self,
        *,
        home_id: str,
        provider: str,
        connection_type: str,
        connected_by: str,
        refresh_token: str,
        access_token: str | None = None,
        token_expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        provider_email: str | None = None,
    ) -> HomeConnection:
        """Create a new connection with encrypted tokens."""
        connection = HomeConnection(
            id=str(uuid.uuid4()),
            home_id=home_id,
            provider=provider,
            connection_type=connection_type,
            connected_by=connected_by,
            refresh_token=encryption_service.encrypt(refresh_token),
            access_token=encryption_service.encrypt(access_token) if access_token else None,
            token_expires_at=token_expires_at,
            scopes=scopes,
            provider_email=provider_email,
            connected_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(connection)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def update_tokens(
        self,
        connection: HomeConnection,
        *,
        refresh_token: str | None = None,
        access_token: str | None = None,
        token_expires_at: datetime | None = None,
        scopes: list[str] | None = None,
        provider_email: str | None = None,
    ) -> HomeConnection:
        """Update connection tokens."""
        if refresh_token is not None:
            connection.refresh_token = encryption_service.encrypt(refresh_token)
        if access_token is not None:
            connection.access_token = encryption_service.encrypt(access_token)
        if token_expires_at is not None:
            connection.token_expires_at = token_expires_at
        if scopes is not None:
            connection.scopes = scopes
        if provider_email is not None:
            connection.provider_email = provider_email
        connection.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(connection)
        return connection

    def delete(self, connection: HomeConnection) -> None:
        """Delete a connection."""
        self.db.delete(connection)
        self.db.commit()

    def get_decrypted_refresh_token(self, connection: HomeConnection) -> str:
        """Get the decrypted refresh token."""
        decrypted = encryption_service.decrypt(connection.refresh_token)
        if decrypted is None:
            raise ValueError("Failed to decrypt refresh token")
        return decrypted

    def get_decrypted_access_token(self, connection: HomeConnection) -> str | None:
        """Get the decrypted access token."""
        return encryption_service.decrypt(connection.access_token)


class McpServerSettingsRepository:
    """Repository for McpServerSettings entities."""

    def __init__(self, db: Session):
        self.db = db

    def get_by_home_and_server(
        self,
        home_id: str,
        mcp_server_id: str,
    ) -> McpServerSettings | None:
        """Get settings for a home and MCP server."""
        stmt = select(McpServerSettings).where(
            McpServerSettings.home_id == home_id,
            McpServerSettings.mcp_server_id == mcp_server_id,
        )
        return self.db.scalar(stmt)

    def get_or_create(
        self,
        home_id: str,
        mcp_server_id: str,
    ) -> McpServerSettings:
        """Get existing settings or create empty defaults."""
        settings = self.get_by_home_and_server(home_id, mcp_server_id)
        if settings:
            return settings
        return self.create(home_id=home_id, mcp_server_id=mcp_server_id)

    def create(
        self,
        *,
        home_id: str,
        mcp_server_id: str,
        settings_json: dict[str, Any] | None = None,
    ) -> McpServerSettings:
        """Create new MCP server settings."""
        settings = McpServerSettings(
            id=str(uuid.uuid4()),
            home_id=home_id,
            mcp_server_id=mcp_server_id,
            settings_json=settings_json or {},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(settings)
        self.db.commit()
        self.db.refresh(settings)
        return settings

    def update(
        self,
        settings: McpServerSettings,
        settings_json: dict[str, Any],
    ) -> McpServerSettings:
        """Update MCP server settings."""
        settings.settings_json = settings_json
        settings.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(settings)
        return settings

    def delete(self, settings: McpServerSettings) -> None:
        """Delete MCP server settings."""
        self.db.delete(settings)
        self.db.commit()
