"""Vivian MCP Server - Household agent tools."""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from vivian_mcp.tools.hsa_tools import HSAToolManager
from vivian_mcp.tools.drive_tools import DriveToolManager
from vivian_mcp.config import Settings


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


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        # HSA Tools
        Tool(
            name="parse_receipt_to_expense_schema",
            description="Parse a receipt PDF and extract structured expense data",
            inputSchema={
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Local path to the PDF receipt file"
                    }
                },
                "required": ["pdf_path"]
            }
        ),
        Tool(
            name="append_expense_to_ledger",
            description="Add an expense to the Google Sheets ledger",
            inputSchema={
                "type": "object",
                "properties": {
                    "expense_json": {
                        "type": "object",
                        "description": "Structured expense data"
                    },
                    "reimbursement_status": {
                        "type": "string",
                        "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                        "description": "Reimbursement status"
                    },
                    "drive_file_id": {
                        "type": "string",
                        "description": "Google Drive file ID for the receipt"
                    }
                },
                "required": ["expense_json", "reimbursement_status", "drive_file_id"]
            }
        ),
        Tool(
            name="update_expense_status",
            description="Update the reimbursement status of an existing expense",
            inputSchema={
                "type": "object",
                "properties": {
                    "expense_id": {
                        "type": "string",
                        "description": "ID of the expense to update"
                    },
                    "new_status": {
                        "type": "string",
                        "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                        "description": "New reimbursement status"
                    },
                    "reimbursement_date": {
                        "type": "string",
                        "format": "date",
                        "description": "Date of reimbursement (if applicable)"
                    }
                },
                "required": ["expense_id", "new_status"]
            }
        ),
        Tool(
            name="get_unreimbursed_balance",
            description="Get total of all unreimbursed expenses",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="bulk_import_receipts_from_directory",
            description="Bulk import all PDF receipts from a directory",
            inputSchema={
                "type": "object",
                "properties": {
                    "directory_path": {
                        "type": "string",
                        "description": "Path to directory containing PDF receipts"
                    },
                    "reimbursement_status_override": {
                        "type": "string",
                        "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                        "description": "Override status for all receipts"
                    }
                },
                "required": ["directory_path"]
            }
        ),
        # Drive Tools
        Tool(
            name="upload_receipt_to_drive",
            description="Upload a receipt PDF to Google Drive in the appropriate folder",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_file_path": {
                        "type": "string",
                        "description": "Local path to the PDF file"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                        "description": "Reimbursement status to determine folder"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename"
                    }
                },
                "required": ["local_file_path", "status"]
            }
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls."""
    try:
        if name == "parse_receipt_to_expense_schema":
            result = await hsa_tools.parse_receipt(arguments["pdf_path"])
            return [TextContent(type="text", text=result)]
            
        elif name == "append_expense_to_ledger":
            result = await hsa_tools.append_to_ledger(
                arguments["expense_json"],
                arguments["reimbursement_status"],
                arguments["drive_file_id"]
            )
            return [TextContent(type="text", text=result)]
            
        elif name == "update_expense_status":
            result = await hsa_tools.update_status(
                arguments["expense_id"],
                arguments["new_status"],
                arguments.get("reimbursement_date")
            )
            return [TextContent(type="text", text=result)]
            
        elif name == "get_unreimbursed_balance":
            result = await hsa_tools.get_unreimbursed_balance()
            return [TextContent(type="text", text=result)]
            
        elif name == "bulk_import_receipts_from_directory":
            result = await hsa_tools.bulk_import(
                arguments["directory_path"],
                arguments.get("reimbursement_status_override")
            )
            return [TextContent(type="text", text=result)]
            
        elif name == "upload_receipt_to_drive":
            result = await drive_tools.upload_receipt(
                arguments["local_file_path"],
                arguments["status"],
                arguments.get("filename")
            )
            return [TextContent(type="text", text=result)]
            
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
            
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    """Main entry point."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
