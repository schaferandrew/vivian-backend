"""MCP server registry and selection helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
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
    settings_schema: list[dict[str, Any]] | None = None


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

        settings_schema = item.get("settings_schema")
        if settings_schema and not isinstance(settings_schema, list):
            settings_schema = None
            
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
            settings_schema=settings_schema,
        )

    return custom_defs


def get_mcp_server_definitions(settings: Settings) -> dict[str, MCPServerDefinition]:
    """Return available MCP servers keyed by stable ID."""
    definitions: dict[str, MCPServerDefinition] = {
        "vivian_hsa": MCPServerDefinition(
            id="vivian_hsa",
            name="Vivian HSA",
            description="Drive + Sheets tools for HSA receipt workflows.",
            command=["python", "-m", "vivian_mcp.server"],
            server_path=settings.resolve_mcp_server_path("mcp-server"),
            default_enabled=True,
            tools=[
                "upload_receipt_to_drive",
                "append_expense_to_ledger",
                "check_for_duplicates",
                "update_expense_status",
                "get_unreimbursed_balance",
                "bulk_import_receipts",
            ],
            source="builtin",
            requires_connection="google",
            settings_schema=[
                {"key": "google_spreadsheet_id", "label": "Google Spreadsheet ID", "type": "string", "required": True},
                {"key": "google_worksheet_name", "label": "Worksheet Name", "type": "string", "required": True, "default": "HSA_Ledger"},
                {"key": "drive_root_folder_id", "label": "Drive Root Folder ID", "type": "string", "required": True},
                {"key": "reimbursed_folder_id", "label": "Reimbursed Folder ID", "type": "string", "required": True},
                {"key": "unreimbursed_folder_id", "label": "Unreimbursed Folder ID", "type": "string", "required": True},
                {"key": "not_eligible_folder_id", "label": "Not Eligible Folder ID", "type": "string", "required": False},
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
        ),
    }

    # Future scaffolding: optional custom servers from settings JSON.
    definitions.update(_load_custom_server_definitions(settings))
    return definitions


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
        dict.fromkeys(server_id for server_id in requested_ids if server_id in allowed_ids)
    )
