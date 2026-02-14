"""Vivian MCP Server - Household agent tools."""

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from vivian_mcp.tools.hsa_tools import HSAToolManager
from vivian_mcp.tools.drive_tools import DriveToolManager
from vivian_mcp.tools.charitable_tools import CharitableToolManager
from vivian_mcp.config import Settings

# Import logging service - we'll copy it to mcp-server or follow similar pattern
logger = logging.getLogger(__name__)

# Simple logging setup for MCP server (will use same pattern as API)
def setup_mcp_logging(settings: Settings) -> None:
    """Initialize logging for MCP server."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler with basic formatting
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    formatter = logging.Formatter(
        '[%(levelname)s] %(asctime)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


@asynccontextmanager
async def app_lifespan(server: Server) -> AsyncIterator[Settings]:
    """Manage application lifecycle."""
    settings = Settings()
    
    # Initialize logging
    if settings.enable_logging:
        setup_mcp_logging(settings)
        logger.info(f"MCP server (environment={settings.environment}, loglevel={settings.log_level})")
    
    yield settings


# Create MCP server
app = Server("vivian-mcp", lifespan=app_lifespan)

# Initialize tool managers
hsa_tools = HSAToolManager()
drive_tools = DriveToolManager()
charitable_tools = CharitableToolManager()


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
                    },
                    "check_duplicates": {
                        "type": "boolean",
                        "description": "Whether to check for duplicates before appending",
                        "default": True
                    },
                    "force_append": {
                        "type": "boolean",
                        "description": "Whether to append even if duplicates are found",
                        "default": False
                    }
                },
                "required": ["expense_json", "reimbursement_status", "drive_file_id"]
            }
        ),
        Tool(
            name="check_for_duplicates",
            description="Check if an expense is a duplicate of existing entries in the ledger",
            inputSchema={
                "type": "object",
                "properties": {
                    "expense_json": {
                        "type": "object",
                        "description": "Expense data to check for duplicates (provider, service_date, amount)"
                    },
                    "fuzzy_days": {
                        "type": "integer",
                        "description": "Number of days to allow for fuzzy date matching",
                        "default": 3
                    }
                },
                "required": ["expense_json"]
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
            name="read_ledger_entries",
            description="Read HSA ledger entries with optional filtering by year and status",
            inputSchema={
                "type": "object",
                "properties": {
                    "year": {
                        "type": "integer",
                        "description": "Optional year to filter entries (e.g., 2025)"
                    },
                    "status_filter": {
                        "type": "string",
                        "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                        "description": "Optional status to filter entries"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of entries to return",
                        "default": 1000
                    }
                }
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
        Tool(
            name="bulk_import_receipts",
            description="Bulk import parsed receipts: upload files and batch append ledger rows",
            inputSchema={
                "type": "object",
                "properties": {
                    "receipts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "local_file_path": {
                                    "type": "string",
                                    "description": "Local path to receipt file"
                                },
                                "expense_json": {
                                    "type": "object",
                                    "description": "Parsed expense payload"
                                },
                                "reimbursement_status": {
                                    "type": "string",
                                    "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                                    "description": "Status/folder for this receipt"
                                },
                                "filename": {
                                    "type": "string",
                                    "description": "Optional filename for upload"
                                }
                            },
                            "required": ["local_file_path", "expense_json", "reimbursement_status"]
                        }
                    },
                    "check_duplicates": {
                        "type": "boolean",
                        "default": True
                    },
                    "force_append": {
                        "type": "boolean",
                        "default": False
                    },
                    "fuzzy_days": {
                        "type": "integer",
                        "default": 3
                    }
                },
                "required": ["receipts"]
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
        # Charitable Donation Tools
        Tool(
            name="upload_charitable_receipt_to_drive",
            description="Upload a charitable donation receipt to Google Drive organized by tax year",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_file_path": {
                        "type": "string",
                        "description": "Local path to the receipt file"
                    },
                    "tax_year": {
                        "type": "string",
                        "description": "Tax year for folder organization (e.g., '2025')"
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional custom filename"
                    }
                },
                "required": ["local_file_path"]
            }
        ),
        Tool(
            name="append_charitable_donation_to_ledger",
            description="Add a charitable donation to the Google Sheets ledger",
            inputSchema={
                "type": "object",
                "properties": {
                    "donation_json": {
                        "type": "object",
                        "description": "Donation data (organization_name, donation_date, amount, tax_deductible, description)"
                    },
                    "drive_file_id": {
                        "type": "string",
                        "description": "Google Drive file ID for the receipt"
                    },
                    "check_duplicates": {
                        "type": "boolean",
                        "description": "Whether to check for duplicates before appending",
                        "default": True
                    },
                    "force_append": {
                        "type": "boolean",
                        "description": "Whether to append even if duplicates are found",
                        "default": False
                    }
                },
                "required": ["donation_json", "drive_file_id"]
            }
        ),
        Tool(
            name="check_charitable_duplicates",
            description="Check if a charitable donation is a duplicate of existing entries",
            inputSchema={
                "type": "object",
                "properties": {
                    "donation_json": {
                        "type": "object",
                        "description": "Donation data to check for duplicates (organization_name, donation_date, amount)"
                    },
                    "fuzzy_days": {
                        "type": "integer",
                        "description": "Number of days to allow for fuzzy date matching",
                        "default": 3
                    }
                },
                "required": ["donation_json"]
            }
        ),
        Tool(
            name="get_charitable_summary",
            description="Get summary of charitable donations by tax year",
            inputSchema={
                "type": "object",
                "properties": {
                    "tax_year": {
                        "type": "string",
                        "description": "Optional tax year to filter by (e.g., '2025')"
                    }
                }
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
                arguments["drive_file_id"],
                arguments.get("check_duplicates", True),
                arguments.get("force_append", False)
            )
            return [TextContent(type="text", text=result)]
            
        elif name == "check_for_duplicates":
            result = await hsa_tools.check_for_duplicates(
                arguments["expense_json"],
                arguments.get("fuzzy_days", 3)
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

        elif name == "read_ledger_entries":
            result = await hsa_tools.read_ledger_entries(
                year=arguments.get("year"),
                status_filter=arguments.get("status_filter"),
                limit=arguments.get("limit", 1000)
            )
            return [TextContent(type="text", text=result)]

        elif name == "bulk_import_receipts_from_directory":
            result = await hsa_tools.bulk_import(
                arguments["directory_path"],
                arguments.get("reimbursement_status_override")
            )
            return [TextContent(type="text", text=result)]

        elif name == "bulk_import_receipts":
            result = await hsa_tools.bulk_import_receipts(
                arguments["receipts"],
                arguments.get("check_duplicates", True),
                arguments.get("force_append", False),
                arguments.get("fuzzy_days", 3),
            )
            return [TextContent(type="text", text=result)]
            
        elif name == "upload_receipt_to_drive":
            result = await drive_tools.upload_receipt(
                arguments["local_file_path"],
                arguments["status"],
                arguments.get("filename")
            )
            return [TextContent(type="text", text=result)]

        # Charitable Donation Tools
        elif name == "upload_charitable_receipt_to_drive":
            result = await charitable_tools.upload_receipt_to_drive(
                arguments["local_file_path"],
                arguments.get("tax_year"),
                arguments.get("filename")
            )
            return [TextContent(type="text", text=result)]

        elif name == "append_charitable_donation_to_ledger":
            result = await charitable_tools.append_donation_to_ledger(
                arguments["donation_json"],
                arguments["drive_file_id"],
                arguments.get("check_duplicates", True),
                arguments.get("force_append", False),
            )
            return [TextContent(type="text", text=result)]

        elif name == "check_charitable_duplicates":
            result = await charitable_tools.check_for_duplicates(
                arguments["donation_json"],
                arguments.get("fuzzy_days", 3)
            )
            return [TextContent(type="text", text=json.dumps(result))]

        elif name == "get_charitable_summary":
            result = await charitable_tools.get_donation_summary(
                arguments.get("tax_year")
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
