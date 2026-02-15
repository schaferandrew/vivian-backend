"""Tests for chat personality system prompt generation."""

from vivian_api.chat.personality import VivianPersonality


def test_get_system_prompt_accepts_mcp_tool_guidance():
    prompt = VivianPersonality.get_system_prompt(
        current_date="2026-02-15",
        user_location="United States",
        enabled_mcp_servers=["hsa_ledger"],
        mcp_tool_guidance=["hsa_ledger tools available: read_ledger_entries"],
    )

    assert "Runtime context:" in prompt
    assert "Enabled MCP servers: hsa_ledger" in prompt
    assert "MCP tool guidance:" in prompt
    assert "hsa_ledger tools available: read_ledger_entries" in prompt


def test_get_system_prompt_without_context_returns_base_prompt():
    prompt = VivianPersonality.get_system_prompt()
    assert prompt == VivianPersonality.SYSTEM_PROMPT
