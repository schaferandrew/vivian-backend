"""Internal API endpoints for the MCP server and other internal consumers.

NOTE: These endpoints are intended for internal Docker network use only.
Infrastructure-level network policy enforces this; no additional network auth
is applied beyond the API key check.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import ApiKeyContext, get_home_from_api_key
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.repositories.connection_repository import HomeConnectionRepository
from vivian_api.services.drive_config import get_or_create_config
from vivian_api.services.google_sheets import (
    append_row,
    build_google_credentials,
    get_drive_service,
    get_sheets_service,
    read_rows,
)

router = APIRouter(tags=["internal"])

settings = Settings()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pad(row: list, length: int) -> list[str]:
    """Pad a row to *length* with empty strings."""
    row = list(row)
    while len(row) < length:
        row.append("")
    return [str(v) for v in row]


def _row_to_hsa_entry(row: list) -> dict:
    r = _pad(row, 11)
    return {
        "id": r[0],
        "provider": r[1],
        "service_date": r[2],
        "paid_date": r[3],
        "amount": _to_float(r[4]),
        "hsa_eligible": r[5],
        "status": r[6],
        "reimbursement_date": r[7],
        "drive_file_id": r[8],
        "confidence": r[9],
        "created_at": r[10],
    }


def _row_to_donation_entry(row: list) -> dict:
    r = _pad(row, 10)
    return {
        "id": r[0],
        "organization_name": r[1],
        "donation_date": r[2],
        "amount": _to_float(r[3]),
        "tax_deductible": r[4],
        "description": r[5],
        "drive_file_id": r[6],
        "tax_year": r[7],
        "confidence": r[8],
        "created_at": r[9],
    }


def _to_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _year_matches(date_str: str, year: int) -> bool:
    return date_str.startswith(str(year))


# ---------------------------------------------------------------------------
# Existing endpoint
# ---------------------------------------------------------------------------


class GoogleTokenResponse(BaseModel):
    refresh_token: str
    email: str | None


@router.get("/internal/google-token", response_model=GoogleTokenResponse)
def get_google_token(
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> GoogleTokenResponse:
    """Return the Google refresh token for the API key's home.

    Used by the MCP server to obtain Google credentials without storing them
    locally.

    Returns:
        200: { refresh_token, email }
        401: invalid_api_key (handled by get_home_from_api_key)
        404: google_not_connected
    """
    repo = HomeConnectionRepository(db)
    connection = repo.get_by_home_and_provider(
        api_key_ctx.home_id,
        provider="google",
        connection_type="drive_sheets",
    )

    if not connection:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "google_not_connected",
                "message": "Google account not connected for this home.",
                "action": "Ask your admin to connect Google in Settings.",
                "settings_url": f"{settings.vivian_url}/settings/connections",
            },
        )

    refresh_token = repo.get_decrypted_refresh_token(connection)
    return GoogleTokenResponse(
        refresh_token=refresh_token,
        email=connection.provider_email,
    )


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@router.get("/internal/capabilities")
def get_capabilities(
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Return which features are available for the home.

    Always includes setup_url so clients can surface it when a feature is
    not yet available.
    """
    repo = HomeConnectionRepository(db)
    connection = repo.get_by_home_and_provider(
        api_key_ctx.home_id,
        provider="google",
        connection_type="drive_sheets",
    )
    return {
        "google_connected": bool(connection),
        "setup_url": f"{settings.vivian_url}/settings/connections",
    }


# ---------------------------------------------------------------------------
# HSA endpoints
# ---------------------------------------------------------------------------


class HSAExpenseBody(BaseModel):
    provider: str
    amount: float
    service_date: Optional[str] = None
    paid_date: Optional[str] = None
    hsa_eligible: bool = True
    status: str = "unreimbursed"
    reimbursement_date: Optional[str] = None
    drive_file_id: Optional[str] = None


@router.post("/internal/hsa/expenses")
async def create_hsa_expense(
    body: HSAExpenseBody,
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Log a new HSA expense to the home's Ledger sheet."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    expense_id = str(uuid.uuid4())
    row = [
        expense_id,
        body.provider,
        body.service_date or "",
        body.paid_date or "",
        str(body.amount),
        str(body.hsa_eligible).lower(),
        body.status,
        body.reimbursement_date or "",
        body.drive_file_id or "",
        "",  # confidence
        _now_iso(),
    ]
    append_row(sheets_svc, cfg["hsa_ledger_id"], "Sheet1", row)
    return {"success": True, "id": expense_id}


@router.get("/internal/hsa/expenses")
async def list_hsa_expenses(
    year: Optional[int] = Query(None),
    status_filter: Optional[str] = Query(None, enum=["reimbursed", "unreimbursed", "not_hsa_eligible"]),
    limit: int = Query(100, ge=1, le=5000),
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """List HSA expenses with optional year and status filtering."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    rows = read_rows(sheets_svc, cfg["hsa_ledger_id"], "Sheet1")
    entries = [_row_to_hsa_entry(r) for r in rows]

    if year:
        entries = [
            e for e in entries
            if _year_matches(e["service_date"], year) or _year_matches(e["paid_date"], year)
        ]
    if status_filter:
        entries = [e for e in entries if e["status"] == status_filter]

    entries = entries[:limit]

    total_amount = sum(e["amount"] for e in entries)
    total_reimbursed = sum(e["amount"] for e in entries if e["status"] == "reimbursed")
    total_unreimbursed = sum(e["amount"] for e in entries if e["status"] == "unreimbursed")
    total_not_eligible = sum(e["amount"] for e in entries if e["status"] == "not_hsa_eligible")
    count_reimbursed = sum(1 for e in entries if e["status"] == "reimbursed")
    count_unreimbursed = sum(1 for e in entries if e["status"] == "unreimbursed")
    count_not_eligible = sum(1 for e in entries if e["status"] == "not_hsa_eligible")

    return {
        "success": True,
        "entries": entries,
        "summary": {
            "total_entries": len(entries),
            "total_amount": total_amount,
            "total_reimbursed": total_reimbursed,
            "total_unreimbursed": total_unreimbursed,
            "total_not_eligible": total_not_eligible,
            "count_reimbursed": count_reimbursed,
            "count_unreimbursed": count_unreimbursed,
            "count_not_eligible": count_not_eligible,
            "available_to_reimburse": total_unreimbursed,
        },
    }


@router.get("/internal/hsa/balance")
async def get_hsa_balance(
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Return the total unreimbursed HSA balance."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    rows = read_rows(sheets_svc, cfg["hsa_ledger_id"], "Sheet1")
    unreimbursed = [
        _row_to_hsa_entry(r) for r in rows
        if len(r) > 6 and r[6] == "unreimbursed"
    ]
    return {
        "total_unreimbursed": sum(e["amount"] for e in unreimbursed),
        "count": len(unreimbursed),
    }


# ---------------------------------------------------------------------------
# Charitable endpoints
# ---------------------------------------------------------------------------


class CharitableEntryBody(BaseModel):
    organization_name: str
    amount: float
    donation_date: Optional[str] = None
    tax_deductible: bool = True
    description: Optional[str] = None
    tax_year: Optional[str] = None


@router.post("/internal/charitable/entries")
async def create_charitable_entry(
    body: CharitableEntryBody,
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Log a new charitable donation to the home's Donations Ledger sheet."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    entry_id = str(uuid.uuid4())
    tax_year = body.tax_year or str(datetime.now(timezone.utc).year)
    row = [
        entry_id,
        body.organization_name,
        body.donation_date or "",
        str(body.amount),
        str(body.tax_deductible).lower(),
        body.description or "",
        "",  # drive_file_id
        tax_year,
        "",  # confidence
        _now_iso(),
    ]
    append_row(sheets_svc, cfg["donations_ledger_id"], "Sheet1", row)
    return {"success": True, "id": entry_id}


@router.get("/internal/charitable/entries")
async def list_charitable_entries(
    tax_year: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=5000),
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """List charitable donations with optional tax year filtering."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    rows = read_rows(sheets_svc, cfg["donations_ledger_id"], "Sheet1")
    entries = [_row_to_donation_entry(r) for r in rows]

    if tax_year:
        entries = [e for e in entries if e["tax_year"] == tax_year]

    entries = entries[:limit]
    return {"success": True, "entries": entries}


@router.get("/internal/charitable/summary")
async def get_charitable_summary(
    tax_year: Optional[str] = Query(None),
    api_key_ctx: ApiKeyContext = Depends(get_home_from_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Return charitable donation totals and breakdown by organization."""
    try:
        cfg = await get_or_create_config(api_key_ctx.home_id, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    creds = build_google_credentials(api_key_ctx.home_id, db, settings)
    sheets_svc = get_sheets_service(creds)

    rows = read_rows(sheets_svc, cfg["donations_ledger_id"], "Sheet1")
    entries = [_row_to_donation_entry(r) for r in rows]

    if tax_year:
        entries = [e for e in entries if e["tax_year"] == tax_year]

    total = sum(e["amount"] for e in entries)
    tax_deductible_total = sum(
        e["amount"] for e in entries if e["tax_deductible"].lower() == "true"
    )

    by_organization: dict[str, float] = {}
    by_year: dict[str, float] = {}
    for e in entries:
        org = e["organization_name"]
        yr = e["tax_year"]
        by_organization[org] = by_organization.get(org, 0.0) + e["amount"]
        by_year[yr] = by_year.get(yr, 0.0) + e["amount"]

    return {
        "total": total,
        "tax_deductible_total": tax_deductible_total,
        "by_organization": by_organization,
        "by_year": by_year,
    }
