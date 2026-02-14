import tempfile
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


class FakeMCPClient:
    last_instance = None

    def __init__(self, *_args, **_kwargs):
        FakeMCPClient.last_instance = self
        self.calls: list[tuple] = []

    async def start(self):
        self.calls.append(("start",))

    async def stop(self):
        self.calls.append(("stop",))

    async def check_for_duplicates(self, expense_json, fuzzy_days: int = 3):
        self.calls.append(("check_for_duplicates", expense_json, fuzzy_days))
        return {"is_duplicate": False, "potential_duplicates": []}

    async def check_charitable_duplicates(self, donation_json):
        self.calls.append(("check_charitable_duplicates", donation_json))
        return {"is_duplicate": False, "potential_duplicates": []}

    async def upload_receipt_to_drive(self, local_file_path: str, status: str, filename=None):
        self.calls.append(("upload_receipt_to_drive", local_file_path, status, filename))
        return {"success": True, "file_id": "drive_hsa"}

    async def append_to_ledger(self, expense_json, status: str, drive_file_id: str, **_kwargs):
        self.calls.append(("append_to_ledger", expense_json, status, drive_file_id))
        return {"success": True, "entry_id": "entry_hsa"}

    async def upload_charitable_receipt_to_drive(self, local_file_path: str, donation_year=None, filename=None):
        self.calls.append(("upload_charitable_receipt_to_drive", local_file_path, donation_year, filename))
        return {"success": True, "file_id": "drive_charitable"}

    async def append_charitable_donation_to_ledger(self, donation_json, drive_file_id: str, **_kwargs):
        self.calls.append(("append_charitable_donation_to_ledger", donation_json, drive_file_id))
        return {"success": True, "entry_id": "entry_charitable"}

    async def bulk_import_receipts(self, *_args, **_kwargs):
        self.calls.append(("bulk_import_receipts",))
        return {
            "success": True,
            "results": [],
            "imported_count": 0,
            "failed_count": 0,
            "total_amount": 0.0,
        }


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch):
    app.dependency_overrides[get_current_user_context] = lambda: DummyUserContext()
    monkeypatch.setattr(receipts, "MCPClient", FakeMCPClient)
    async def _fake_create_mcp_client(*_args, **_kwargs):
        return FakeMCPClient()
    monkeypatch.setattr(receipts, "_create_mcp_client", _fake_create_mcp_client)
    yield
    app.dependency_overrides.clear()


def _write_temp_receipt(tmp_path: Path) -> str:
    temp_file = tmp_path / "receipt.pdf"
    temp_file.write_text("test")
    return str(temp_file)




def test_confirm_receipt_hsa_success(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "temp_file_path": temp_file_path,
        "category": "hsa",
        "expense_data": {
            "provider": "Health Clinic",
            "service_date": "2024-01-01",
            "amount": 42.5,
            "hsa_eligible": True,
        },
        "status": "unreimbursed",
    }

    response = client.post("/api/v1/receipts/confirm", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["drive_file_id"] == "drive_hsa"

    assert FakeMCPClient.last_instance is not None
    calls = FakeMCPClient.last_instance.calls
    assert any(call[0] == "check_for_duplicates" for call in calls)
    assert ("upload_receipt_to_drive", temp_file_path, "unreimbursed", None) in calls
    assert any(call[0] == "append_to_ledger" for call in calls)


def test_confirm_receipt_charitable_success(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "temp_file_path": temp_file_path,
        "category": "charitable",
        "charitable_data": {
            "organization_name": "Red Cross",
            "donation_date": "2024-02-10",
            "amount": 120.0,
            "tax_deductible": True,
            "description": "Annual appeal",
        },
    }

    response = client.post("/api/v1/receipts/confirm", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["drive_file_id"] == "drive_charitable"

    assert FakeMCPClient.last_instance is not None
    calls = FakeMCPClient.last_instance.calls
    assert any(call[0] == "upload_charitable_receipt_to_drive" for call in calls)
    assert any(call[0] == "append_charitable_donation_to_ledger" for call in calls)
    assert not any(call[0] == "check_for_duplicates" for call in calls)


def test_confirm_receipt_charitable_requires_data(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "temp_file_path": temp_file_path,
        "category": "charitable",
    }

    response = client.post("/api/v1/receipts/confirm", json=payload)
    assert response.status_code == 422


def test_confirm_receipt_hsa_requires_data(monkeypatch, tmp_path):
    monkeypatch.setattr(receipts.settings, "temp_upload_dir", str(tmp_path))
    temp_file_path = _write_temp_receipt(tmp_path)
    client = TestClient(app)

    payload = {
        "temp_file_path": temp_file_path,
        "category": "hsa",
        "status": "unreimbursed",
    }

    response = client.post("/api/v1/receipts/confirm", json=payload)
    assert response.status_code == 422


def test_bulk_import_confirm_rejects_missing_expense():
    client = TestClient(app)
    response = client.post(
        "/api/v1/receipts/bulk-import/confirm",
        json={
            "items": [{"temp_file_path": "/tmp/receipt.pdf", "category": "hsa"}],
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["failed_count"] == 1
