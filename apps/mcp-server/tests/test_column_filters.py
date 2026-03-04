"""Tests for shared column filter helpers."""

from __future__ import annotations

from vivian_mcp.tools.google_common import apply_column_filters


def test_apply_column_filters_combines_multiple_filters():
    headers = ["provider", "amount", "status"]
    rows = [
        ["Clinic A", "40", "unreimbursed"],
        ["Clinic B", "80", "reimbursed"],
        ["Clinic C", "120", "unreimbursed"],
    ]

    result = apply_column_filters(
        headers=headers,
        rows=rows,
        column_filters=[
            {"column": "status", "operator": "equals", "value": "unreimbursed"},
            {"column": "amount", "operator": "gte", "value": 100},
        ],
    )

    assert result["success"] is True
    assert result["rows"] == [["Clinic C", "120", "unreimbursed"]]


def test_apply_column_filters_validates_unknown_columns():
    result = apply_column_filters(
        headers=["provider", "amount"],
        rows=[["Clinic", "40"]],
        column_filters=[{"column": "missing", "value": "x"}],
    )

    assert result["success"] is False
    assert "Unknown column" in result["error"]
    assert result["available_columns"] == ["amount", "provider"]
