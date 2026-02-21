"""FastMCP wiring tests for Vivian MCP server."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp import FastMCP

from vivian_mcp import server


@pytest.mark.asyncio
async def test_server_uses_fastmcp_and_emits_output_schema():
    assert isinstance(server.app, FastMCP)

    tools = await server.app.list_tools()
    by_name = {tool.name: tool for tool in tools}

    assert "read_ledger_entries" in by_name
    read_tool_dump = by_name["read_ledger_entries"].model_dump()

    assert "inputSchema" in read_tool_dump
    assert "outputSchema" in read_tool_dump
    assert "column_filters" in read_tool_dump["inputSchema"]["properties"]


@pytest.mark.asyncio
async def test_call_tool_returns_structured_payload(monkeypatch):
    async def fake_get_unreimbursed_balance() -> str:
        return json.dumps({"total_unreimbursed": 42.5, "count": 3})

    monkeypatch.setattr(server.hsa_tools, "get_unreimbursed_balance", fake_get_unreimbursed_balance)

    content, structured = await server.app.call_tool("get_unreimbursed_balance", {})

    assert structured["total_unreimbursed"] == 42.5
    assert structured["count"] == 3
    assert len(content) == 1
    assert "42.5" in content[0].text


@pytest.mark.asyncio
async def test_read_ledger_entries_passes_column_filters(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_read_ledger_entries(
        year=None,
        status_filter=None,
        limit=1000,
        column_filters=None,
    ) -> dict:
        captured["year"] = year
        captured["status_filter"] = status_filter
        captured["limit"] = limit
        captured["column_filters"] = column_filters
        return {"success": True, "entries": [], "summary": {}}

    monkeypatch.setattr(server.hsa_tools, "read_ledger_entries", fake_read_ledger_entries)

    _, structured = await server.app.call_tool(
        "read_ledger_entries",
        {
            "year": 2026,
            "status_filter": "unreimbursed",
            "limit": 25,
            "column_filters": [
                {
                    "column": "provider",
                    "operator": "contains",
                    "value": "clinic",
                    "case_sensitive": False,
                }
            ],
        },
    )

    assert structured["success"] is True
    assert captured == {
        "year": 2026,
        "status_filter": "unreimbursed",
        "limit": 25,
        "column_filters": [
            {
                "column": "provider",
                "operator": "contains",
                "value": "clinic",
                "case_sensitive": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_append_cash_charitable_donation_tool_is_registered():
    tools = await server.app.list_tools()
    by_name = {tool.name: tool for tool in tools}

    assert "append_cash_charitable_donation_to_ledger" in by_name


@pytest.mark.asyncio
async def test_append_cash_charitable_donation_routes_without_drive_file_id(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_append_cash_donation_to_ledger(
        donation_json,
        check_duplicates=True,
        force_append=False,
    ):
        captured["donation_json"] = donation_json
        captured["check_duplicates"] = check_duplicates
        captured["force_append"] = force_append
        return {"success": True, "entry_id": "cash123", "tax_year": "2026"}

    monkeypatch.setattr(
        server.charitable_tools,
        "append_cash_donation_to_ledger",
        fake_append_cash_donation_to_ledger,
    )

    _, structured = await server.app.call_tool(
        "append_cash_charitable_donation_to_ledger",
        {
            "donation_json": {
                "organization_name": "Red Cross",
                "donation_date": "2026-03-10",
                "amount": 100,
            },
            "check_duplicates": False,
            "force_append": True,
        },
    )

    assert structured["success"] is True
    assert structured["entry_id"] == "cash123"
    assert captured == {
        "donation_json": {
            "organization_name": "Red Cross",
            "donation_date": "2026-03-10",
            "amount": 100,
        },
        "check_duplicates": False,
        "force_append": True,
    }
