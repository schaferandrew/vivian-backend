"""Deterministic tests for MCP addition tool.

These tests ensure the addition operation behaves predictably 
for MCP protocol validation.
"""

import json
import pytest
from vivian_test_mcp.server import call_tool


class TestAdditionDeterminism:
    """Tests that addition produces deterministic, correct results."""
    
    @pytest.mark.asyncio
    async def test_whole_numbers(self):
        """2 + 3 should equal 5."""
        result = await call_tool("add_numbers", {"a": 2, "b": 3})
        parsed = json.loads(result[0].text)
        
        assert parsed["success"] is True
        assert parsed["sum"] == 5.0
    
    @pytest.mark.asyncio
    async def test_decimal_addition(self):
        """3.2 + 3.2 should equal 6.4."""
        result = await call_tool("add_numbers", {"a": 3.2, "b": 3.2})
        parsed = json.loads(result[0].text)
        
        assert parsed["sum"] == 6.4
    
    @pytest.mark.asyncio
    async def test_decimals_resulting_in_whole(self):
        """3.2 + 3.8 should equal 7.0."""
        result = await call_tool("add_numbers", {"a": 3.2, "b": 3.8})
        parsed = json.loads(result[0].text)
        
        assert parsed["sum"] == 7.0
    
    @pytest.mark.asyncio
    async def test_negative_numbers(self):
        """-5 + 3 should equal -2."""
        result = await call_tool("add_numbers", {"a": -5, "b": 3})
        parsed = json.loads(result[0].text)
        
        assert parsed["sum"] == -2.0
    
    @pytest.mark.asyncio
    async def test_mixed_types(self):
        """Integer inputs work with float inputs."""
        result = await call_tool("add_numbers", {"a": 5, "b": 2.5})
        parsed = json.loads(result[0].text)
        
        assert parsed["sum"] == 7.5
