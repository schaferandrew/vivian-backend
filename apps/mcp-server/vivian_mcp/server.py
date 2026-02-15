"""Vivian MCP Server - Household agent tools."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from pydantic import ValidationError

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

try:
    from mcp.types import CallToolResult
except Exception:  # pragma: no cover - older MCP SDK variants
    CallToolResult = None  # type: ignore[assignment]

from vivian_mcp.config import Settings
from vivian_mcp.contracts import (
    TOOL_CONTRACTS,
    get_tool_contract,
    validate_tool_input,
    validate_tool_output,
)
from vivian_mcp.tools.charitable_tools import CharitableToolManager
from vivian_mcp.tools.drive_tools import DriveToolManager
from vivian_mcp.tools.hsa_tools import HSAToolManager


@asynccontextmanager
async def app_lifespan(server: Server) -> AsyncIterator[Settings]:
    """Manage application lifecycle."""
    settings = Settings()
    yield settings


# Create MCP server
app = Server("vivian-mcp", lifespan=app_lifespan)

# Initialize tool managers
hsa_tools = HSAToolManager()
drive_tools = DriveToolManager()
charitable_tools = CharitableToolManager()

_TOOL_SUPPORTS_OUTPUT_SCHEMA = "outputSchema" in getattr(Tool, "model_fields", {})
_CALL_TOOL_SUPPORTS_STRUCTURED = bool(
    CallToolResult and "structuredContent" in getattr(CallToolResult, "model_fields", {})
)


def _parse_manager_payload(raw_result: Any) -> dict[str, Any]:
    """Normalize manager return values to dict payloads."""
    if isinstance(raw_result, dict):
        return raw_result

    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
            if isinstance(parsed, dict):
                return parsed
            return {"success": True, "value": parsed}
        except Exception:
            return {"success": False, "error": raw_result}

    return {"success": False, "error": f"Unsupported tool response type: {type(raw_result).__name__}"}


def _call_tool_response(payload: dict[str, Any], *, is_error: bool = False):
    """Build MCP tool response, preferring structuredContent when available."""
    text = json.dumps(payload, default=str)
    text_part = TextContent(type="text", text=text)

    if _CALL_TOOL_SUPPORTS_STRUCTURED:
        return CallToolResult(  # type: ignore[misc]
            content=[text_part],
            structuredContent=payload,
            isError=is_error,
        )

    return [text_part]


async def _execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch validated tool call and return validated payload."""
    if name == "parse_receipt_to_expense_schema":
        raw_result = await hsa_tools.parse_receipt(arguments["pdf_path"])
    elif name == "append_expense_to_ledger":
        raw_result = await hsa_tools.append_to_ledger(
            arguments["expense_json"],
            arguments["reimbursement_status"],
            arguments["drive_file_id"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
        )
    elif name == "check_for_duplicates":
        raw_result = await hsa_tools.check_for_duplicates(
            arguments["expense_json"],
            arguments.get("fuzzy_days", 3),
        )
    elif name == "update_expense_status":
        raw_result = await hsa_tools.update_status(
            arguments["expense_id"],
            arguments["new_status"],
            arguments.get("reimbursement_date"),
        )
    elif name == "get_unreimbursed_balance":
        raw_result = await hsa_tools.get_unreimbursed_balance()
    elif name == "read_ledger_entries":
        raw_result = await hsa_tools.read_ledger_entries(
            year=arguments.get("year"),
            status_filter=arguments.get("status_filter"),
            limit=arguments.get("limit", 1000),
            column_filters=arguments.get("column_filters"),
        )
    elif name == "bulk_import_receipts_from_directory":
        raw_result = await hsa_tools.bulk_import(
            arguments["directory_path"],
            arguments.get("reimbursement_status_override"),
        )
    elif name == "bulk_import_receipts":
        raw_result = await hsa_tools.bulk_import_receipts(
            arguments["receipts"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
            arguments.get("fuzzy_days", 3),
        )
    elif name == "upload_receipt_to_drive":
        raw_result = await drive_tools.upload_receipt(
            arguments["local_file_path"],
            arguments["status"],
            arguments.get("filename"),
        )
    elif name == "upload_charitable_receipt_to_drive":
        raw_result = await charitable_tools.upload_receipt_to_drive(
            arguments["local_file_path"],
            arguments.get("tax_year"),
            arguments.get("filename"),
        )
    elif name == "append_charitable_donation_to_ledger":
        raw_result = await charitable_tools.append_donation_to_ledger(
            arguments["donation_json"],
            arguments["drive_file_id"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
        )
    elif name == "check_charitable_duplicates":
        raw_result = await charitable_tools.check_for_duplicates(
            arguments["donation_json"],
            arguments.get("fuzzy_days", 3),
        )
    elif name == "get_charitable_summary":
        raw_result = await charitable_tools.get_donation_summary(
            arguments.get("tax_year"),
            arguments.get("column_filters"),
        )
    elif name == "read_charitable_ledger_entries":
        raw_result = await charitable_tools.read_donation_entries(
            tax_year=arguments.get("tax_year"),
            organization=arguments.get("organization"),
            tax_deductible=arguments.get("tax_deductible"),
            limit=arguments.get("limit", 1000),
            column_filters=arguments.get("column_filters"),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")

    return _parse_manager_payload(raw_result)


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools from typed contracts."""
    tools: list[Tool] = []
    for contract in TOOL_CONTRACTS:
        payload: dict[str, Any] = {
            "name": contract.name,
            "description": contract.description,
            "inputSchema": contract.input_schema(),
        }
        if _TOOL_SUPPORTS_OUTPUT_SCHEMA:
            payload["outputSchema"] = contract.output_schema()
        tools.append(Tool(**payload))
    return tools


@app.call_tool()
async def call_tool(name: str, arguments: dict | None = None):
    """Handle tool calls with contract validation and structured response payloads."""
    contract = get_tool_contract(name)
    if not contract:
        return _call_tool_response(
            {"success": False, "error": f"Unknown tool: {name}"},
            is_error=True,
        )

    try:
        validated_args = validate_tool_input(name, arguments or {})
    except ValidationError as exc:
        return _call_tool_response(
            {
                "success": False,
                "error": "Invalid tool arguments",
                "details": exc.errors(),
            },
            is_error=True,
        )

    try:
        payload = await _execute_tool(name, validated_args)
        validated_payload = validate_tool_output(name, payload)
        is_error = bool(validated_payload.get("success") is False)
        return _call_tool_response(validated_payload, is_error=is_error)
    except ValidationError as exc:
        return _call_tool_response(
            {
                "success": False,
                "error": "Tool output failed contract validation",
                "details": exc.errors(),
            },
            is_error=True,
        )
    except Exception as exc:
        return _call_tool_response(
            {
                "success": False,
                "error": str(exc),
            },
            is_error=True,
        )


async def main():
    """Main entry point."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
