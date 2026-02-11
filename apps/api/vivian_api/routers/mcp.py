"""MCP server discovery, settings, and diagnostics router."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import (
    CurrentUserContext,
    get_current_user_context,
    require_roles,
)
from vivian_api.config import Settings, get_enabled_mcp_servers, set_enabled_mcp_servers
from vivian_api.db.database import get_db
from vivian_api.repositories.connection_repository import McpServerSettingsRepository
from vivian_api.services.mcp_client import MCPClient, MCPClientError
from vivian_api.services.mcp_registry import get_mcp_server_definitions, normalize_enabled_server_ids


router = APIRouter(
    prefix="/mcp",
    tags=["mcp"],
    dependencies=[Depends(get_current_user_context)],
)
logger = logging.getLogger(__name__)


class MCPServerSettingsSchema(BaseModel):
    """Schema definition for a single MCP setting field."""

    key: str
    label: str
    type: str  # "string", "number", "boolean", etc.
    required: bool = False
    default: Any = None


class MCPServerInfo(BaseModel):
    """Metadata for one MCP server option."""

    id: str
    name: str
    description: str
    tools: list[str]
    default_enabled: bool
    enabled: bool
    source: str
    requires_connection: str | None = None
    settings_schema: list[MCPServerSettingsSchema] | None = None
    settings: dict[str, Any] | None = None
    editable: bool = False


class MCPServersResponse(BaseModel):
    """List available MCP servers and enabled state."""

    servers: list[MCPServerInfo]
    enabled_server_ids: list[str]


class MCPEnabledUpdateRequest(BaseModel):
    """Request to update global enabled MCP server IDs."""

    enabled_server_ids: list[str] = Field(default_factory=list)


class MCPEnabledUpdateResponse(BaseModel):
    """Response after updating enabled MCP server IDs."""

    enabled_server_ids: list[str]


class MCPSettingsResponse(BaseModel):
    """Response with MCP server settings."""

    mcp_server_id: str
    settings: dict[str, Any]
    settings_schema: list[MCPServerSettingsSchema]
    editable: bool


class MCPSettingsUpdateRequest(BaseModel):
    """Request to update MCP server settings."""

    settings: dict[str, Any]


class MCPSettingsUpdateResponse(BaseModel):
    """Response after updating MCP server settings."""

    mcp_server_id: str
    settings: dict[str, Any]


class MCPAddTestRequest(BaseModel):
    """Request payload for testing addition MCP server."""

    a: float
    b: float
    server_id: str = "test_addition"


class MCPAddTestResponse(BaseModel):
    """Response payload for addition test result."""

    server_id: str
    a: float
    b: float
    sum: float


def _get_default_home_id(current_user: CurrentUserContext) -> str:
    """Get the user's default home ID."""
    if not current_user.default_membership:
        raise HTTPException(status_code=400, detail="No home membership found")
    return str(current_user.default_membership.home_id)


def _is_owner(current_user: CurrentUserContext) -> bool:
    """Check if user has owner role in any membership."""
    return any(m.role == "owner" for m in current_user.memberships)


@router.get("/servers", response_model=MCPServersResponse)
async def list_mcp_servers(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """List available MCP servers with current settings for user's home."""
    settings = Settings()
    definitions = get_mcp_server_definitions(settings)
    enabled_ids = normalize_enabled_server_ids(get_enabled_mcp_servers(), settings)
    enabled_lookup = set(enabled_ids)
    
    # Get user's home settings
    home_id = _get_default_home_id(current_user)
    settings_repo = McpServerSettingsRepository(db)
    is_editable = _is_owner(current_user)
    
    servers = []
    for definition in definitions.values():
        # Get current settings for this server
        mcp_settings = settings_repo.get_by_home_and_server(home_id, definition.id)
        current_settings = mcp_settings.settings_json if mcp_settings else {}
        
        # Build settings schema
        schema = None
        if definition.settings_schema:
            schema = [
                MCPServerSettingsSchema(
                    key=s["key"],
                    label=s["label"],
                    type=s.get("type", "string"),
                    required=s.get("required", False),
                    default=s.get("default"),
                )
                for s in definition.settings_schema
            ]
        
        servers.append(
            MCPServerInfo(
                id=definition.id,
                name=definition.name,
                description=definition.description,
                tools=definition.tools,
                default_enabled=definition.default_enabled,
                enabled=definition.id in enabled_lookup,
                source=definition.source,
                requires_connection=definition.requires_connection,
                settings_schema=schema,
                settings=current_settings if schema else None,
                editable=is_editable,
            )
        )
    
    return MCPServersResponse(servers=servers, enabled_server_ids=enabled_ids)


@router.get("/servers/{server_id}/settings", response_model=MCPSettingsResponse)
async def get_mcp_server_settings(
    server_id: str,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get settings for a specific MCP server."""
    settings = Settings()
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(server_id)
    
    if not definition:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server: {server_id}")
    
    home_id = _get_default_home_id(current_user)
    settings_repo = McpServerSettingsRepository(db)
    mcp_settings = settings_repo.get_or_create(home_id, server_id)
    
    # Build settings schema
    schema = []
    if definition.settings_schema:
        schema = [
            MCPServerSettingsSchema(
                key=s["key"],
                label=s["label"],
                type=s.get("type", "string"),
                required=s.get("required", False),
                default=s.get("default"),
            )
            for s in definition.settings_schema
        ]
    
    return MCPSettingsResponse(
        mcp_server_id=server_id,
        settings=mcp_settings.settings_json,
        settings_schema=schema,
        editable=_is_owner(current_user),
    )


@router.put("/servers/{server_id}/settings", response_model=MCPSettingsUpdateResponse)
async def update_mcp_server_settings(
    server_id: str,
    request: MCPSettingsUpdateRequest,
    current_user: CurrentUserContext = Depends(require_roles("owner")),
    db: Session = Depends(get_db),
):
    """Update settings for a specific MCP server (owner only)."""
    settings = Settings()
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(server_id)
    
    if not definition:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server: {server_id}")
    
    # Validate settings against schema
    if definition.settings_schema:
        allowed_keys = {s["key"] for s in definition.settings_schema}
        for key in request.settings.keys():
            if key not in allowed_keys:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid setting key: {key}. Allowed: {', '.join(sorted(allowed_keys))}",
                )
        
        # Check required fields
        for schema_field in definition.settings_schema:
            if schema_field.get("required") and not request.settings.get(schema_field["key"]):
                raise HTTPException(
                    status_code=400,
                    detail=f"Required setting missing: {schema_field['key']}",
                )
    
    home_id = _get_default_home_id(current_user)
    settings_repo = McpServerSettingsRepository(db)
    mcp_settings = settings_repo.get_or_create(home_id, server_id)
    updated = settings_repo.update(mcp_settings, request.settings)
    
    return MCPSettingsUpdateResponse(
        mcp_server_id=server_id,
        settings=updated.settings_json,
    )


@router.post("/servers/enabled", response_model=MCPEnabledUpdateResponse)
async def update_enabled_mcp_servers(
    request: MCPEnabledUpdateRequest,
    _current_user: CurrentUserContext = Depends(require_roles("owner", "parent")),
):
    """Update globally enabled MCP server IDs."""
    settings = Settings()
    normalized_ids = normalize_enabled_server_ids(request.enabled_server_ids, settings)
    set_enabled_mcp_servers(normalized_ids)
    return MCPEnabledUpdateResponse(enabled_server_ids=normalized_ids)


@router.post("/test/add", response_model=MCPAddTestResponse)
async def test_addition_server(request: MCPAddTestRequest):
    """Call the test addition MCP server to verify protocol wiring."""
    settings = Settings()
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(request.server_id)
    if not definition:
        raise HTTPException(status_code=404, detail=f"Unknown MCP server: {request.server_id}")

    if "add_numbers" not in definition.tools:
        raise HTTPException(
            status_code=400,
            detail=f"MCP server '{request.server_id}' does not expose add_numbers",
        )

    mcp_client = MCPClient(
        definition.command,
        server_path_override=definition.server_path,
    )
    logger.info(
        "MCP addition test requested. server_id=%s a=%s b=%s",
        request.server_id,
        request.a,
        request.b,
    )
    await mcp_client.start()
    try:
        result = await mcp_client.add_numbers(request.a, request.b)
        if not result.get("success"):
            raise HTTPException(status_code=502, detail=result.get("error", "Addition test failed"))

        return MCPAddTestResponse(
            server_id=request.server_id,
            a=float(result.get("a", request.a)),
            b=float(result.get("b", request.b)),
            sum=float(result.get("sum", request.a + request.b)),
        )
    except MCPClientError as exc:
        raise HTTPException(status_code=502, detail=f"MCP test call failed: {exc}") from exc
    finally:
        await mcp_client.stop()
