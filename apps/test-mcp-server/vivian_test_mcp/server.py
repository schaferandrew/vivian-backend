"""Minimal MCP server for testing tool calls from the API."""

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool


app = Server("vivian-test-mcp")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Expose simple test tools."""
    return [
        Tool(
            name="add_numbers",
            description="Add two numbers and return the sum.",
            inputSchema={
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "First number"},
                    "b": {"type": "number", "description": "Second number"},
                },
                "required": ["a", "b"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute test tools."""
    if name != "add_numbers":
        return [TextContent(type="text", text=json.dumps({"success": False, "error": f"Unknown tool: {name}"}))]

    a = float(arguments["a"])
    b = float(arguments["b"])
    return [
        TextContent(
            type="text",
            text=json.dumps({"success": True, "a": a, "b": b, "sum": a + b}),
        )
    ]


async def _main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main() -> None:
    """CLI entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()
