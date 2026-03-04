"""MCP client parsing tests for structured and legacy text tool results."""

import sys
import types

import pytest

# Stub MCP modules before importing app modules.
mcp_module = types.ModuleType("mcp")
mcp_module.ClientSession = object
sys.modules.setdefault("mcp", mcp_module)

mcp_stdio = types.ModuleType("mcp.client.stdio")
mcp_stdio.StdioServerParameters = object
mcp_stdio.stdio_client = lambda *args, **kwargs: None
sys.modules.setdefault("mcp.client.stdio", mcp_stdio)

mcp_types = types.ModuleType("mcp.types")
mcp_types.TextContent = object
sys.modules.setdefault("mcp.types", mcp_types)

from vivian_api.services.mcp_client import (
    MCPClient,
    extract_tool_result_payload,
    extract_tool_result_text,
)


def test_extract_tool_result_payload_prefers_structured_content():
    result = {
        "structured_content": {"success": True, "total_unreimbursed": 123.45, "count": 2},
        "content": [{"type": "text", "text": '{"success": false}'}],
    }
    payload = extract_tool_result_payload(result)
    assert payload == {"success": True, "total_unreimbursed": 123.45, "count": 2}


def test_extract_tool_result_payload_falls_back_to_text_json():
    result = {
        "content": [{"type": "text", "text": '{"success": true, "total": 88.0}'}],
    }
    payload = extract_tool_result_payload(result)
    assert payload == {"success": True, "total": 88.0}


def test_extract_tool_result_text_handles_missing_content():
    assert extract_tool_result_text({}) == "{}"


@pytest.mark.asyncio
async def test_client_method_uses_structured_payload(monkeypatch):
    client = MCPClient(["python", "-m", "vivian_mcp.server"])

    async def fake_call_tool(_tool_name, _arguments):
        return {
            "structured_content": {"total_unreimbursed": 55.0, "count": 4},
            "content": [{"type": "text", "text": '{"total_unreimbursed": 1.0, "count": 1}'}],
        }

    monkeypatch.setattr(client, "call_tool", fake_call_tool)
    result = await client.get_unreimbursed_balance()
    assert result == {"total_unreimbursed": 55.0, "count": 4}


@pytest.mark.asyncio
async def test_client_method_falls_back_to_text_payload(monkeypatch):
    client = MCPClient(["python", "-m", "vivian_mcp.server"])

    async def fake_call_tool(_tool_name, _arguments):
        return {
            "content": [{"type": "text", "text": '{"success": true, "entries": [], "summary": {}}'}],
        }

    monkeypatch.setattr(client, "call_tool", fake_call_tool)
    result = await client.read_ledger_entries(limit=5)
    assert result == {"success": True, "entries": [], "summary": {}}
