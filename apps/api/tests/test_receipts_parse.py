from pathlib import Path
import sys
import types

import pytest
from fastapi.testclient import TestClient

# Stub MCP modules before importing app.
mcp_module = types.ModuleType("mcp")
mcp_module.ClientSession = object
sys.modules["mcp"] = mcp_module

mcp_stdio = types.ModuleType("mcp.client.stdio")
mcp_stdio.StdioServerParameters = object
mcp_stdio.stdio_client = lambda *args, **kwargs: None
sys.modules["mcp.client.stdio"] = mcp_stdio

mcp_types = types.ModuleType("mcp.types")
mcp_types.TextContent = object
sys.modules["mcp.types"] = mcp_types

from vivian_api.main import app
from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.routers import receipts


class DummyUserContext:
    user = None
    memberships = []
    default_membership = types.SimpleNamespace(home_id="00000000-0000-0000-0000-000000000001")


class FakeOpenRouterService:
    def __init__(self, payload: dict):
        self.payload = payload

    async def parse_receipt(self, _pdf_path: str) -> dict:
        return self.payload

    async def close(self):
        return None


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch):
    app.dependency_overrides[get_current_user_context] = lambda: DummyUserContext()
    yield
    app.dependency_overrides.clear()


def _write_temp_receipt(tmp_path: Path) -> str:
    temp_file = tmp_path / "receipt.pdf"
    temp_file.write_text("test")
    return str(temp_file)


def test_parse_receipt_charitable_full(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "success": True,
        "parsed_data": {
            "category": "charitable",
            "organization_name": "Saint Thomas More Catholic Church",
            "donation_date": "2026-01-11",
            "amount": 500.0,
            "tax_deductible": True,
            "description": "Annual donation",
        },
        "raw_output": "raw",
    }
    monkeypatch.setattr(receipts, "OpenRouterService", lambda: FakeOpenRouterService(payload))

    response = client.post("/api/v1/receipts/parse", json={"temp_file_path": temp_file_path})
    assert response.status_code == 200
    parsed = response.json()["parsed_data"]
    assert parsed["suggested_category"] == "charitable"
    assert parsed["charitable_data"]["organization_name"] == "Saint Thomas More Catholic Church"
    assert parsed["expense"]["provider"] == "Saint Thomas More Catholic Church"


def test_parse_receipt_charitable_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "success": True,
        "parsed_data": {
            "category": "charitable",
            "amount": 75.0,
        },
        "raw_output": "raw",
    }
    monkeypatch.setattr(receipts, "OpenRouterService", lambda: FakeOpenRouterService(payload))

    response = client.post("/api/v1/receipts/parse", json={"temp_file_path": temp_file_path})
    assert response.status_code == 200
    parsed = response.json()["parsed_data"]
    assert parsed["suggested_category"] == "charitable"
    assert parsed["charitable_data"]["organization_name"] == "Unknown Organization"
    assert parsed["charitable_data"]["amount"] == 75.0


def test_parse_receipt_hsa_full(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "success": True,
        "parsed_data": {
            "category": "hsa",
            "provider": "Health Clinic",
            "service_date": "2026-01-11",
            "paid_date": "2026-01-12",
            "amount": 42.5,
            "hsa_eligible": True,
        },
        "raw_output": "raw",
    }
    monkeypatch.setattr(receipts, "OpenRouterService", lambda: FakeOpenRouterService(payload))

    response = client.post("/api/v1/receipts/parse", json={"temp_file_path": temp_file_path})
    assert response.status_code == 200
    parsed = response.json()["parsed_data"]
    assert parsed["suggested_category"] == "hsa"
    assert parsed["expense"]["provider"] == "Health Clinic"
    assert parsed["charitable_data"]["organization_name"] == "Health Clinic"


def test_parse_receipt_hsa_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "success": True,
        "parsed_data": {
            "category": "hsa",
            "amount": 10.0,
        },
        "raw_output": "raw",
    }
    monkeypatch.setattr(receipts, "OpenRouterService", lambda: FakeOpenRouterService(payload))

    response = client.post("/api/v1/receipts/parse", json={"temp_file_path": temp_file_path})
    assert response.status_code == 200
    parsed = response.json()["parsed_data"]
    assert parsed["suggested_category"] == "hsa"
    assert parsed["expense"]["provider"] == "Unknown Provider"
