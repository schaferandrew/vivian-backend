"""MCP server registry and selection helpers."""

from dataclasses import dataclass

from vivian_api.config import Settings


@dataclass(frozen=True)
class MCPServerDefinition:
    """Static definition of a known MCP server."""

    id: str
    name: str
    description: str
    command: list[str]
    server_path: str
    default_enabled: bool
    tools: list[str]
    source: str = "builtin"


def get_mcp_server_definitions(settings: Settings) -> dict[str, MCPServerDefinition]:
    """Return available MCP servers keyed by stable ID."""
    return {
        "vivian_hsa": MCPServerDefinition(
            id="vivian_hsa",
            name="Vivian HSA",
            description="Drive + Sheets tools for receipt workflows.",
            command=["python", "-m", "vivian_mcp.server"],
            server_path=settings.mcp_server_path("mcp-server"),
            default_enabled=True,
            tools=[
                "upload_receipt_to_drive",
                "append_expense_to_ledger",
                "update_expense_status",
                "get_unreimbursed_balance",
            ],
        ),
        "test_addition": MCPServerDefinition(
            id="test_addition",
            name="Test Addition",
            description="Minimal MCP server with add_numbers(a, b).",
            command=["python", "-m", "vivian_test_mcp.server"],
            server_path=settings.mcp_server_path("test-mcp-server"),
            default_enabled=False,
            tools=["add_numbers"],
        ),
    }


def normalize_enabled_server_ids(
    requested_ids: list[str] | None,
    settings: Settings,
) -> list[str]:
    """Validate and normalize enabled server IDs against known registry."""
    definitions = get_mcp_server_definitions(settings)
    allowed_ids = set(definitions.keys())

    if requested_ids is None:
        defaults = [
            server_id.strip()
            for server_id in settings.mcp_default_enabled_servers.split(",")
            if server_id.strip()
        ]
        requested_ids = defaults or [
            server_id
            for server_id, definition in definitions.items()
            if definition.default_enabled
        ]

    return list(
        dict.fromkeys(
            server_id
            for server_id in requested_ids
            if server_id in allowed_ids
        )
    )
