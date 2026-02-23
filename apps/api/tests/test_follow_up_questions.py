"""Tests for structured follow-up question helpers."""

from __future__ import annotations

from vivian_api.chat.follow_up_questions import (
    build_missing_fields_follow_up_question,
    extract_donation_updates_from_message,
)


def test_build_missing_fields_follow_up_question_includes_template_and_pending_merge():
    question_bundle = build_missing_fields_follow_up_question(
        tool_name="append_cash_charitable_donation_to_ledger",
        server_id="charitable_ledger",
        arguments={
            "donation_json": {
                "amount": 100,
            },
            "check_duplicates": True,
        },
        raw_tool_output="""
        {
          "success": false,
          "error": "Missing required donation fields: organization name, donation date.",
          "missing_fields": ["organization_name", "donation_date"],
          "normalized_donation_json": {"amount": 100.0, "description": "Cash donation"}
        }
        """,
    )

    assert question_bundle is not None
    question, pending = question_bundle
    assert question["tool_name"] == "append_cash_charitable_donation_to_ledger"
    assert question["server_id"] == "charitable_ledger"
    assert question["missing_fields"] == ["organization_name", "donation_date"]
    assert "Please reply with" in question["prompt"]
    assert "organization_name:" in question["prompt"]
    assert pending["arguments"]["donation_json"]["amount"] == 100.0
    assert pending["arguments"]["donation_json"]["description"] == "Cash donation"


def test_extract_donation_updates_from_message_parses_labeled_text():
    updates = extract_donation_updates_from_message(
        "organization: Luminary Coffee House, date: 2026-02-23, amount: $100.00",
        ["organization_name", "donation_date", "amount"],
    )

    assert updates["organization_name"] == "Luminary Coffee House"
    assert updates["donation_date"] == "2026-02-23"
    assert updates["amount"] == "100.00"


def test_extract_donation_updates_from_message_parses_json_aliases():
    updates = extract_donation_updates_from_message(
        '{"organization":"Luminary Coffee House","date":"02/23/2026","amount":100}',
        ["organization_name", "donation_date", "amount"],
    )

    assert updates["organization_name"] == "Luminary Coffee House"
    assert updates["donation_date"] == "02/23/2026"
    assert updates["amount"] == 100


def test_extract_donation_updates_from_message_single_org_fallback():
    updates = extract_donation_updates_from_message(
        "Luminary Coffee House",
        ["organization_name"],
    )

    assert updates == {"organization_name": "Luminary Coffee House"}


def test_extract_donation_updates_from_message_cash_confirmation_yes_no():
    yes_updates = extract_donation_updates_from_message(
        "cash_confirmation: yes",
        ["cash_confirmation"],
    )
    no_updates = extract_donation_updates_from_message(
        "has_receipt: yes",
        ["cash_confirmation"],
    )

    assert yes_updates == {"cash_confirmation": True}
    assert no_updates == {"cash_confirmation": False}
