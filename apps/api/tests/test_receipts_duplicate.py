"""Tests for the check-duplicate and check-charitable-duplicate endpoints."""

import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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

from vivian_api.main import app
from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.db.database import get_db
from vivian_api.routers import receipts


class DummyUserContext:
    user = None
    memberships = []
    default_membership = types.SimpleNamespace(home_id="00000000-0000-0000-0000-000000000001")


class FakeMCPClientNoDuplicates:
    """MCP client that always reports no duplicates."""

    def __init__(self, *_args, **_kwargs):
        self.calls: list[tuple] = []

    async def start(self):
        self.calls.append(("start",))

    async def stop(self):
        self.calls.append(("stop",))

    async def check_for_duplicates(self, expense_json, fuzzy_days=3):
        self.calls.append(("check_for_duplicates", expense_json))
        return {"is_duplicate": False, "potential_duplicates": []}

    async def check_charitable_duplicates(self, donation_json, fuzzy_days=3):
        self.calls.append(("check_charitable_duplicates", donation_json))
        return {"is_duplicate": False, "potential_duplicates": []}


class FakeMCPClientWithDuplicates:
    """MCP client that always reports a duplicate."""

    def __init__(self, *_args, **_kwargs):
        self.calls: list[tuple] = []

    async def start(self):
        self.calls.append(("start",))

    async def stop(self):
        self.calls.append(("stop",))

    async def check_for_duplicates(self, expense_json, fuzzy_days=3):
        self.calls.append(("check_for_duplicates", expense_json))
        return {
            "is_duplicate": True,
            "potential_duplicates": [
                {
                    "entry_id": "row_42",
                    "provider": "Clinic",
                    "service_date": "2026-01-01",
                    "amount": 42.5,
                    "status": "unreimbursed",
                    "match_type": "exact",
                }
            ],
        }

    async def check_charitable_duplicates(self, donation_json, fuzzy_days=3):
        self.calls.append(("check_charitable_duplicates", donation_json))
        return {
            "is_duplicate": True,
            "potential_duplicates": [
                {
                    "entry_id": "row_99",
                    "provider": "Red Cross",
                    "amount": 120.0,
                    "service_date": "2026-02-10",
                    "status": "recorded",
                    "match_type": "exact",
                }
            ],
        }


@pytest.fixture(autouse=True)
def _override_dependencies(monkeypatch):
    app.dependency_overrides[get_current_user_context] = lambda: DummyUserContext()
    app.dependency_overrides[get_db] = lambda: None  # db not needed when _create_mcp_client is mocked
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# HSA duplicate check
# ---------------------------------------------------------------------------

def test_check_hsa_duplicate_no_match(monkeypatch):
    async def _fake_create_mcp_client(*_args, **_kwargs):
        return FakeMCPClientNoDuplicates()

    monkeypatch.setattr(receipts, "_create_mcp_client", _fake_create_mcp_client)
    client = TestClient(app)

    response = client.post("/api/v1/receipts/check-duplicate", json={
        "expense_data": {
            "provider": "Health Clinic",
            "service_date": "2026-01-01",
            "amount": 42.5,
            "hsa_eligible": True,
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["is_duplicate"] is False
    assert data["recommendation"] == "import"


def test_check_hsa_duplicate_with_match(monkeypatch):
    async def _fake_create_mcp_client(*_args, **_kwargs):
        return FakeMCPClientWithDuplicates()

    monkeypatch.setattr(receipts, "_create_mcp_client", _fake_create_mcp_client)
    client = TestClient(app)

    response = client.post("/api/v1/receipts/check-duplicate", json={
        "expense_data": {
            "provider": "Clinic",
            "service_date": "2026-01-01",
            "amount": 42.5,
            "hsa_eligible": True,
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["is_duplicate"] is True
    assert len(data["duplicate_info"]) == 1
    assert data["duplicate_info"][0]["match_type"] == "exact"


# ---------------------------------------------------------------------------
# Charitable duplicate check
# ---------------------------------------------------------------------------

def test_check_charitable_duplicate_no_match(monkeypatch):
    async def _fake_create_mcp_client(*_args, **_kwargs):
        return FakeMCPClientNoDuplicates()

    monkeypatch.setattr(receipts, "_create_mcp_client", _fake_create_mcp_client)
    client = TestClient(app)

    response = client.post("/api/v1/receipts/check-charitable-duplicate", json={
        "charitable_data": {
            "organization_name": "Red Cross",
            "donation_date": "2026-02-10",
            "amount": 120.0,
            "tax_deductible": True,
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["is_duplicate"] is False
    assert data["recommendation"] == "import"


def test_check_charitable_duplicate_with_match(monkeypatch):
    async def _fake_create_mcp_client(*_args, **_kwargs):
        return FakeMCPClientWithDuplicates()

    monkeypatch.setattr(receipts, "_create_mcp_client", _fake_create_mcp_client)
    client = TestClient(app)

    response = client.post("/api/v1/receipts/check-charitable-duplicate", json={
        "charitable_data": {
            "organization_name": "Red Cross",
            "donation_date": "2026-02-10",
            "amount": 120.0,
            "tax_deductible": True,
        }
    })
    assert response.status_code == 200
    data = response.json()
    assert data["is_duplicate"] is True
    assert len(data["duplicate_info"]) == 1


def test_check_charitable_duplicate_requires_data():
    """Should return 422 when charitable_data is missing."""
    client = TestClient(app)
    response = client.post("/api/v1/receipts/check-charitable-duplicate", json={})
    assert response.status_code == 422
