"""Unit tests for charitable donation normalization and validation."""

from __future__ import annotations

import json

import pytest

from vivian_mcp.tools.charitable_tools import CharitableToolManager


def test_normalize_donation_payload_accepts_aliases():
    manager = CharitableToolManager()

    normalized, missing_fields = manager._normalize_donation_payload(
        {
            "organization": " Luminary Coffee House ",
            "date": "02/23/2026",
            "amount": "$100.00",
            "notes": "Cash donation to support local business",
            "tax_deductible": "no",
        }
    )

    assert missing_fields == []
    assert normalized["organization_name"] == "Luminary Coffee House"
    assert normalized["donation_date"] == "2026-02-23"
    assert normalized["amount"] == 100.0
    assert normalized["tax_deductible"] is False
    assert normalized["description"] == "Cash donation to support local business"


@pytest.mark.asyncio
async def test_append_cash_donation_requires_org_and_date_and_cash_confirmation():
    manager = CharitableToolManager()

    result = json.loads(
        await manager.append_cash_donation_to_ledger(
            {
                "amount": 100,
            }
        )
    )

    assert result["success"] is False
    assert "organization_name" in result["missing_fields"]
    assert "donation_date" in result["missing_fields"]
    assert "cash_confirmation" in result["missing_fields"]
    assert "Ask the user to provide" in result["error"]


@pytest.mark.asyncio
async def test_check_for_duplicates_requests_missing_fields_without_sheet_call(monkeypatch):
    manager = CharitableToolManager()
    called = False

    async def fake_get_all_rows(**_kwargs):
        nonlocal called
        called = True
        return {"success": True, "headers": [], "rows": []}

    monkeypatch.setattr(manager, "get_all_rows", fake_get_all_rows)

    result = await manager.check_for_duplicates(
        {
            "amount": 100,
        }
    )

    assert called is False
    assert result["recommendation"] == "needs_input"
    assert "organization_name" in result["missing_fields"]
    assert "donation_date" in result["missing_fields"]


@pytest.mark.asyncio
async def test_append_donation_to_ledger_normalizes_aliases(monkeypatch):
    manager = CharitableToolManager()
    captured: dict[str, object] = {}

    monkeypatch.setattr(manager, "_resolve_spreadsheet", lambda: ("sheet_123", "Charitable Donations"))

    async def fake_ensure_worksheet_exists(**_kwargs):
        return {"success": True}

    async def fake_check_for_duplicates(_donation_json, _fuzzy_days=3):
        return {
            "is_duplicate": False,
            "potential_duplicates": [],
            "recommendation": "import",
        }

    async def fake_append_row(**kwargs):
        captured["row_data"] = kwargs["row_data"]
        return {"success": True}

    monkeypatch.setattr(manager, "ensure_worksheet_exists", fake_ensure_worksheet_exists)
    monkeypatch.setattr(manager, "check_for_duplicates", fake_check_for_duplicates)
    monkeypatch.setattr(manager, "append_row", fake_append_row)

    result = json.loads(
        await manager.append_donation_to_ledger(
            {
                "organization": "Luminary Coffee House",
                "date": "02/23/2026",
                "amount": "100",
                "tax_deductible": "false",
                "notes": "Support local business",
            },
            "cash_donation_no_receipt",
        )
    )

    assert result["success"] is True
    assert captured["row_data"][1] == "Luminary Coffee House"
    assert captured["row_data"][2] == "2026-02-23"
    assert captured["row_data"][3] == 100.0
    assert captured["row_data"][4] == "No"
    assert captured["row_data"][5] == "Support local business"


@pytest.mark.asyncio
async def test_append_cash_donation_rejects_when_not_confirmed_cash():
    manager = CharitableToolManager()

    result = json.loads(
        await manager.append_cash_donation_to_ledger(
            {
                "organization_name": "Luminary Coffee House",
                "donation_date": "2026-02-23",
                "amount": 100,
                "payment_method": "card",
            }
        )
    )

    assert result["success"] is False
    assert result["recommended_action"] == "upload_receipt"
    assert result["next_tool"] == "upload_charitable_receipt_to_drive"
    assert "not confirmed as cash" in result["error"]


@pytest.mark.asyncio
async def test_append_cash_donation_accepts_confirmed_cash(monkeypatch):
    manager = CharitableToolManager()
    captured: dict[str, object] = {}

    async def fake_append_donation_to_ledger(
        donation_json,
        drive_file_id,
        check_duplicates=True,
        force_append=False,
    ):
        captured["donation_json"] = donation_json
        captured["drive_file_id"] = drive_file_id
        captured["check_duplicates"] = check_duplicates
        captured["force_append"] = force_append
        return json.dumps({"success": True, "entry_id": "cash123", "tax_year": "2026"})

    monkeypatch.setattr(manager, "append_donation_to_ledger", fake_append_donation_to_ledger)

    result = json.loads(
        await manager.append_cash_donation_to_ledger(
            {
                "organization": "Luminary Coffee House",
                "date": "02/23/2026",
                "amount": "100",
                "cash_confirmation": "yes",
            },
            check_duplicates=False,
            force_append=True,
        )
    )

    assert result["success"] is True
    assert captured["drive_file_id"] == "cash_donation_no_receipt"
    assert captured["check_duplicates"] is False
    assert captured["force_append"] is True
    assert captured["donation_json"]["is_cash_donation"] is True
