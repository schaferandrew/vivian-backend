"""MCP server discovery and diagnostics router."""

from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException

from vivian_api.config import Settings, get_enabled_mcp_servers, set_enabled_mcp_servers
from vivian_api.services.mcp_client import MCPClient, MCPClientError
from vivian_api.services.mcp_registry import get_mcp_server_definitions, normalize_enabled_server_ids


router = APIRouter(prefix="/mcp", tags=["mcp"])


class MCPServerInfo(BaseModel):
    """Metadata for one MCP server option."""

    id: str
    name: str
    description: str
    tools: list[str]
    default_enabled: bool
    enabled: bool


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


@router.get("/servers", response_model=MCPServersResponse)
async def list_mcp_servers():
    """List available MCP servers and current enabled IDs."""
    settings = Settings()
    definitions = get_mcp_server_definitions(settings)
    enabled_ids = normalize_enabled_server_ids(get_enabled_mcp_servers(), settings)
    enabled_lookup = set(enabled_ids)

    servers = [
        MCPServerInfo(
            id=definition.id,
            name=definition.name,
            description=definition.description,
            tools=definition.tools,
            default_enabled=definition.default_enabled,
            enabled=definition.id in enabled_lookup,
        )
        for definition in definitions.values()
    ]
    return MCPServersResponse(servers=servers, enabled_server_ids=enabled_ids)


@router.post("/servers/enabled", response_model=MCPEnabledUpdateResponse)
async def update_enabled_mcp_servers(request: MCPEnabledUpdateRequest):
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
