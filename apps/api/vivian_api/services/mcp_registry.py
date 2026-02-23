"""MCP server registry and selection helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

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
    requires_connection: str | None = None  # e.g., "google" for Google OAuth
    required_settings: list[dict[str, Any]] = field(default_factory=list)
    # Each setting: {"key": str, "label": str, "type": "folder_id" | "spreadsheet_id" | "text"}


@dataclass
class MCPServerStatus:
    """Runtime status of an MCP server for a specific home."""

    server_id: str
    enabled: bool
    status: str  # "available" | "blocked" | "configured"
    blocked_reason: str | None = None
    settings: dict[str, str] = field(default_factory=dict)
    required_settings: list[dict[str, Any]] = field(default_factory=list)


def _load_custom_server_definitions(settings: Settings) -> dict[str, MCPServerDefinition]:
    """Parse optional JSON-based custom server definitions from settings."""
    if not settings.mcp_custom_servers_json:
        return {}

    try:
        parsed = json.loads(settings.mcp_custom_servers_json)
    except Exception:
        return {}

    if not isinstance(parsed, list):
        return {}

    custom_defs: dict[str, MCPServerDefinition] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue

        server_id = str(item.get("id") or "").strip()
        if not server_id:
            continue

        command = item.get("command")
        if not isinstance(command, list) or not command:
            continue

        tools = item.get("tools")
        if not isinstance(tools, list):
            tools = []

        custom_defs[server_id] = MCPServerDefinition(
            id=server_id,
            name=str(item.get("name") or server_id),
            description=str(item.get("description") or "Custom MCP server"),
            command=[str(v) for v in command if str(v).strip()],
            server_path=str(item.get("server_path") or ""),
            default_enabled=bool(item.get("default_enabled", False)),
            tools=[str(v) for v in tools if str(v).strip()],
            source="custom",
            requires_connection=item.get("requires_connection"),
            required_settings=item.get("required_settings", []),
        )

    return custom_defs


def get_mcp_server_definitions(settings: Settings) -> dict[str, MCPServerDefinition]:
    """Return available MCP servers keyed by stable ID."""
    definitions: dict[str, MCPServerDefinition] = {
        "hsa_ledger": MCPServerDefinition(
            id="hsa_ledger",
            name="HSA Ledger",
            description="Drive + Sheets tools for HSA receipt workflows, including filtered ledger reads.",
            command=["python", "-m", "vivian_mcp.server"],
            server_path=settings.resolve_mcp_server_path("mcp-server"),
            default_enabled=False,  # Must be configured first
            tools=[
                "upload_receipt_to_drive",
                "append_expense_to_ledger",
                "check_for_duplicates",
                "update_expense_status",
                "get_unreimbursed_balance",
                "read_ledger_entries",
                "bulk_import_receipts",
            ],
            source="builtin",
            requires_connection="google",
            required_settings=[
                {"key": "drive_reimbursed_folder_id", "label": "Reimbursed Folder ID", "type": "folder_id"},
                {"key": "drive_unreimbursed_folder_id", "label": "Unreimbursed Folder ID", "type": "folder_id"},
                {"key": "spreadsheet_id", "label": "Spreadsheet ID", "type": "spreadsheet_id"},
            ],
        ),
        "charitable_ledger": MCPServerDefinition(
            id="charitable_ledger",
            name="Charitable Ledger",
            description="Drive + Sheets tools for charitable donation workflows, including filtered summaries.",
            command=["python", "-m", "vivian_mcp.server"],
            server_path=settings.resolve_mcp_server_path("mcp-server"),
            default_enabled=False,  # Must be configured first
            tools=[
                "upload_charitable_receipt_to_drive",
                "append_charitable_donation_to_ledger",
                "append_cash_charitable_donation_to_ledger",
                "check_charitable_duplicates",
                "get_charitable_summary",
                "read_charitable_ledger_entries",
            ],
            source="builtin",
            requires_connection="google",
            required_settings=[
                {"key": "drive_folder_id", "label": "Drive Folder ID", "type": "folder_id"},
                {"key": "spreadsheet_id", "label": "Spreadsheet ID", "type": "spreadsheet_id"},
            ],
        ),
        "test_addition": MCPServerDefinition(
            id="test_addition",
            name="Test Addition",
            description="Minimal MCP server with add_numbers(a, b).",
            command=["python", "-m", "vivian_test_mcp.server"],
            server_path=settings.resolve_mcp_server_path("test-mcp-server"),
            default_enabled=False,
            tools=["add_numbers"],
            source="builtin",
            required_settings=[],
        ),
    }

    # Future scaffolding: optional custom servers from settings JSON.
    definitions.update(_load_custom_server_definitions(settings))
    return definitions


def compute_server_status(
    definition: MCPServerDefinition,
    current_settings: dict[str, str],
    enabled: bool,
) -> MCPServerStatus:
    """Compute the runtime status of a server based on settings."""
    required = definition.required_settings
    missing = []
    
    for setting in required:
        key = setting["key"]
        if not current_settings.get(key):
            missing.append(setting["label"])
    
    if missing:
        return MCPServerStatus(
            server_id=definition.id,
            enabled=False,  # Cannot be enabled if settings missing
            status="blocked",
            blocked_reason=f"Missing: {', '.join(missing)}",
            settings=current_settings,
            required_settings=required,
        )
    
    return MCPServerStatus(
        server_id=definition.id,
        enabled=enabled,
        status="available" if enabled else "configured",
        blocked_reason=None,
        settings=current_settings,
        required_settings=required,
    )


def normalize_enabled_server_ids(
    requested_ids: list[str] | None,
    settings: Settings,
    server_statuses: dict[str, MCPServerStatus] | None = None,
) -> list[str]:
    """Validate and normalize enabled server IDs against known registry.

    Only allows enabling servers that are not blocked if server_statuses is provided.
    """
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

    # Filter out blocked servers
    valid_ids = []
    for server_id in requested_ids:
        if server_id not in allowed_ids:
            continue
        if server_statuses:
            status = server_statuses.get(server_id)
            if status and status.status == "blocked":
                continue  # Cannot enable blocked servers
        valid_ids.append(server_id)

    return list(dict.fromkeys(valid_ids))
