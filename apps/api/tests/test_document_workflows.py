"""Tests for the unified document workflow handler."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Stub MCP modules before importing app.
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

from vivian_api.chat.document_workflows import (
    ChatAttachment,
    DocumentWorkflowArtifact,
    _compute_charitable_confidence,
    _compute_receipt_confidence,
    _infer_category,
    _run_receipt_workflow,
    execute_document_workflows,
    get_document_workflow_registry,
)
from vivian_api.config import Settings
from vivian_shared.models import ExpenseCategory


# ---------------------------------------------------------------------------
# _infer_category tests
# ---------------------------------------------------------------------------

class TestInferCategory:
    def test_hsa_by_default(self):
        assert _infer_category({}) == ExpenseCategory.HSA

    def test_hsa_explicit_category(self):
        assert _infer_category({"category": "hsa"}) == ExpenseCategory.HSA

    def test_charitable_explicit_category(self):
        assert _infer_category({"category": "charitable"}) == ExpenseCategory.CHARITABLE

    def test_charitable_by_organization_name(self):
        assert _infer_category({"organization_name": "Red Cross"}) == ExpenseCategory.CHARITABLE

    def test_charitable_by_donation_date(self):
        assert _infer_category({"donation_date": "2026-01-01"}) == ExpenseCategory.CHARITABLE

    def test_charitable_by_tax_deductible(self):
        assert _infer_category({"tax_deductible": True}) == ExpenseCategory.CHARITABLE

    def test_category_case_insensitive(self):
        assert _infer_category({"category": "CHARITABLE"}) == ExpenseCategory.CHARITABLE
        assert _infer_category({"category": "Charitable"}) == ExpenseCategory.CHARITABLE

    def test_category_with_whitespace(self):
        assert _infer_category({"category": "  charitable  "}) == ExpenseCategory.CHARITABLE

    def test_empty_category_string_defaults_hsa(self):
        assert _infer_category({"category": ""}) == ExpenseCategory.HSA


# ---------------------------------------------------------------------------
# Confidence tests
# ---------------------------------------------------------------------------

class TestComputeReceiptConfidence:
    def test_full_data(self):
        assert _compute_receipt_confidence({
            "provider": "Clinic",
            "service_date": "2026-01-01",
            "amount": 42.5,
        }) == 0.9

    def test_missing_provider(self):
        assert _compute_receipt_confidence({
            "service_date": "2026-01-01",
            "amount": 42.5,
        }) == pytest.approx(0.7)

    def test_missing_service_date(self):
        assert _compute_receipt_confidence({
            "provider": "Clinic",
            "amount": 42.5,
        }) == pytest.approx(0.7)

    def test_missing_amount(self):
        assert _compute_receipt_confidence({
            "provider": "Clinic",
            "service_date": "2026-01-01",
        }) == pytest.approx(0.6)

    def test_zero_amount(self):
        assert _compute_receipt_confidence({
            "provider": "Clinic",
            "service_date": "2026-01-01",
            "amount": 0,
        }) == pytest.approx(0.6)

    def test_all_missing_floors_at_zero(self):
        assert _compute_receipt_confidence({}) == pytest.approx(0.2)


class TestComputeCharitableConfidence:
    def test_full_data(self):
        assert _compute_charitable_confidence({
            "organization_name": "Red Cross",
            "donation_date": "2026-01-01",
            "amount": 100.0,
        }) == 0.9

    def test_missing_organization(self):
        assert _compute_charitable_confidence({
            "donation_date": "2026-01-01",
            "amount": 100.0,
        }) == pytest.approx(0.6)

    def test_missing_donation_date(self):
        assert _compute_charitable_confidence({
            "organization_name": "Red Cross",
            "amount": 100.0,
        }) == pytest.approx(0.7)

    def test_all_missing_floors_at_zero(self):
        assert _compute_charitable_confidence({}) == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# _run_receipt_workflow tests
# ---------------------------------------------------------------------------

class FakeParser:
    def __init__(self, result: dict):
        self._result = result

    async def parse_receipt(self, _path: str) -> dict:
        return self._result

    async def close(self):
        pass


class FakeMCPClient:
    def __init__(self):
        self.calls: list[tuple] = []

    async def start(self):
        self.calls.append(("start",))

    async def stop(self):
        self.calls.append(("stop",))

    async def check_for_duplicates(self, expense_json, fuzzy_days=3):
        self.calls.append(("check_for_duplicates",))
        return {"is_duplicate": False, "potential_duplicates": []}

    async def check_charitable_duplicates(self, donation_json):
        self.calls.append(("check_charitable_duplicates",))
        return {"is_duplicate": False, "potential_duplicates": []}


class FakeDefinition:
    id = "hsa_ledger"
    command = ["python", "-m", "vivian_mcp.server"]
    server_path = None


class FakeCharitableDefinition:
    id = "charitable_ledger"
    command = ["python", "-m", "vivian_mcp.server"]
    server_path = None


def _make_settings(tmp_path: Path) -> Settings:
    """Build a minimal Settings object for tests."""
    return Settings(temp_upload_dir=str(tmp_path))


def _make_attachment(tmp_path: Path, filename: str = "receipt.pdf") -> ChatAttachment:
    temp_file = tmp_path / filename
    temp_file.write_text("test")
    return ChatAttachment(
        temp_file_path=str(temp_file),
        filename=filename,
        document_type="receipt",
    )


class TestRunReceiptWorkflow:
    @pytest.mark.asyncio
    async def test_hsa_receipt_success(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = _make_attachment(tmp_path)

        fake_parser = FakeParser({
            "success": True,
            "parsed_data": {
                "category": "hsa",
                "provider": "Health Clinic",
                "service_date": "2026-01-01",
                "amount": 42.5,
                "hsa_eligible": True,
            },
            "raw_output": "raw",
        })
        fake_mcp = FakeMCPClient()

        with (
            patch("vivian_api.chat.document_workflows.OpenRouterService", return_value=fake_parser),
            patch("vivian_api.chat.document_workflows.MCPClient", return_value=fake_mcp),
            patch(
                "vivian_api.chat.document_workflows.get_mcp_server_definitions",
                return_value={"hsa_ledger": FakeDefinition()},
            ),
        ):
            artifact, tools = await _run_receipt_workflow(
                attachment, ["hsa_ledger"], settings
            )

        assert artifact.status == "ready_for_confirmation"
        assert artifact.document_type == "receipt"
        assert artifact.parsed_data is not None
        assert artifact.parsed_data["suggested_category"] == "hsa"
        assert artifact.parsed_data["expense"]["provider"] == "Health Clinic"
        assert artifact.parsed_data["confidence"] == pytest.approx(0.9)
        # Charitable data should also be populated (for category switching)
        assert artifact.parsed_data["charitable_data"]["organization_name"] == "Health Clinic"

    @pytest.mark.asyncio
    async def test_charitable_receipt_success(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = _make_attachment(tmp_path)

        fake_parser = FakeParser({
            "success": True,
            "parsed_data": {
                "category": "charitable",
                "organization_name": "Red Cross",
                "donation_date": "2026-02-10",
                "amount": 120.0,
                "tax_deductible": True,
                "description": "Annual appeal",
            },
            "raw_output": "raw",
        })
        fake_mcp = FakeMCPClient()

        with (
            patch("vivian_api.chat.document_workflows.OpenRouterService", return_value=fake_parser),
            patch("vivian_api.chat.document_workflows.MCPClient", return_value=fake_mcp),
            patch(
                "vivian_api.chat.document_workflows.get_mcp_server_definitions",
                return_value={"charitable_ledger": FakeCharitableDefinition()},
            ),
        ):
            artifact, tools = await _run_receipt_workflow(
                attachment, ["charitable_ledger"], settings
            )

        assert artifact.status == "ready_for_confirmation"
        assert artifact.parsed_data["suggested_category"] == "charitable"
        assert artifact.parsed_data["charitable_data"]["organization_name"] == "Red Cross"
        assert artifact.parsed_data["confidence"] == pytest.approx(0.9)
        assert "Charitable receipt parsed" in artifact.message

    @pytest.mark.asyncio
    async def test_parse_failure_returns_parse_error(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = _make_attachment(tmp_path)

        fake_parser = FakeParser({"success": False, "error": "Bad PDF"})

        with patch("vivian_api.chat.document_workflows.OpenRouterService", return_value=fake_parser):
            artifact, tools = await _run_receipt_workflow(
                attachment, [], settings
            )

        assert artifact.status == "parse_error"
        assert "Bad PDF" in artifact.message

    @pytest.mark.asyncio
    async def test_invalid_file_path_returns_parse_error(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = ChatAttachment(
            temp_file_path="/etc/passwd",
            filename="malicious.pdf",
            document_type="receipt",
        )

        artifact, tools = await _run_receipt_workflow(
            attachment, [], settings
        )

        assert artifact.status == "parse_error"
        assert "Could not access" in artifact.message

    @pytest.mark.asyncio
    async def test_mcp_disabled_gives_duplicate_check_error(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = _make_attachment(tmp_path)

        fake_parser = FakeParser({
            "success": True,
            "parsed_data": {
                "provider": "Clinic",
                "service_date": "2026-01-01",
                "amount": 42.5,
                "hsa_eligible": True,
            },
            "raw_output": "",
        })

        with patch("vivian_api.chat.document_workflows.OpenRouterService", return_value=fake_parser):
            artifact, tools = await _run_receipt_workflow(
                attachment, [], settings  # no enabled MCP servers
            )

        assert artifact.status == "ready_for_confirmation"
        assert artifact.duplicate_check_error is not None
        assert "disabled" in artifact.duplicate_check_error.lower()


# ---------------------------------------------------------------------------
# execute_document_workflows tests
# ---------------------------------------------------------------------------

class TestExecuteDocumentWorkflows:
    @pytest.mark.asyncio
    async def test_no_attachments(self, tmp_path):
        settings = _make_settings(tmp_path)
        result = await execute_document_workflows(
            attachments=[],
            enabled_mcp_servers=[],
            settings=settings,
        )
        assert result.response_text == "No attachments were provided."
        assert result.artifacts == []

    @pytest.mark.asyncio
    async def test_unsupported_document_type(self, tmp_path):
        settings = _make_settings(tmp_path)
        # ChatAttachment validates document_type, so we construct one and
        # then override the field to simulate an unknown type arriving.
        attachment = ChatAttachment(
            temp_file_path="/tmp/file.txt",
            filename="file.txt",
            document_type="receipt",
        )
        # Force an unsupported type by mutating the validated model
        attachment.__dict__["document_type"] = "unknown"
        result = await execute_document_workflows(
            attachments=[attachment],
            enabled_mcp_servers=[],
            settings=settings,
        )
        assert len(result.artifacts) == 1
        assert result.artifacts[0].status == "unsupported"

    @pytest.mark.asyncio
    async def test_single_attachment_uses_artifact_message(self, tmp_path):
        settings = _make_settings(tmp_path)
        attachment = _make_attachment(tmp_path)

        fake_parser = FakeParser({
            "success": True,
            "parsed_data": {
                "provider": "Clinic",
                "service_date": "2026-01-01",
                "amount": 42.5,
            },
            "raw_output": "",
        })

        with patch("vivian_api.chat.document_workflows.OpenRouterService", return_value=fake_parser):
            result = await execute_document_workflows(
                attachments=[attachment],
                enabled_mcp_servers=[],
                settings=settings,
            )

        assert len(result.artifacts) == 1
        assert result.response_text == result.artifacts[0].message


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_all_receipt_types_registered(self):
        registry = get_document_workflow_registry()
        assert "receipt" in registry
        assert "hsa_receipt" in registry
        assert "charitable_receipt" in registry
        # All should point to the same handler
        assert registry["receipt"] is registry["hsa_receipt"]
        assert registry["receipt"] is registry["charitable_receipt"]
