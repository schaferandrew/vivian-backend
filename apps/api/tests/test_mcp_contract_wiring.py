"""Contract wiring tests for MCP tool schemas and chat tool exposure."""

import sys
import types
from pathlib import Path

import pytest
from pydantic import ValidationError

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

from vivian_mcp.contracts import (
    build_model_tool_specs,
    get_tool_contract,
    validate_tool_input,
)
from vivian_api.config import Settings
from vivian_api.services.mcp_registry import get_mcp_server_definitions


def test_chat_router_uses_contract_builder_for_model_tools():
    router_path = (
        Path(__file__).resolve().parents[1]
        / "vivian_api"
        / "chat"
        / "router.py"
    )
    source = router_path.read_text()
    assert "MODEL_MCP_TOOL_SPECS: dict[str, dict[str, Any]] = build_model_tool_specs()" in source


def test_chat_model_tool_schema_uses_contract_parameters():
    specs = build_model_tool_specs()
    contract = get_tool_contract("read_ledger_entries")
    assert contract is not None
    assert specs["read_ledger_entries"]["parameters"] == contract.input_schema()


def test_charitable_write_tools_are_model_visible():
    specs = build_model_tool_specs()

    for tool_name in (
        "append_charitable_donation_to_ledger",
        "append_cash_charitable_donation_to_ledger",
        "check_charitable_duplicates",
    ):
        contract = get_tool_contract(tool_name)
        assert contract is not None
        assert specs[tool_name]["server_id"] == "charitable_ledger"
        assert specs[tool_name]["parameters"] == contract.input_schema()


def test_contract_input_validation_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        validate_tool_input(
            "read_ledger_entries",
            {
                "limit": 20,
                "unknown": "value",
            },
        )


def test_charitable_registry_lists_cash_append_tool():
    definitions = get_mcp_server_definitions(Settings())
    charitable = definitions["charitable_ledger"]
    assert "append_cash_charitable_donation_to_ledger" in charitable.tools
