"""Ledger and balance router."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import (
    CurrentUserContext,
    get_current_user_context,
)
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.models.schemas import UnreimbursedBalanceResponse
from vivian_api.services.drive_config import get_or_create_config
from vivian_api.services.google_sheets import (
    build_google_credentials,
    get_sheets_service,
    read_rows,
)


router = APIRouter(
    prefix="/ledger",
    tags=["ledger"],
    dependencies=[Depends(get_current_user_context)],
)
settings = Settings()


class LedgerSummary(BaseModel):
    """Summary of HSA ledger entries."""
    total_entries: int
    total_amount: float
    total_reimbursed: float
    total_unreimbursed: float
    total_not_eligible: float
    count_reimbursed: int
    count_unreimbursed: int
    count_not_eligible: int
    available_to_reimburse: float


class LedgerSummaryResponse(BaseModel):
    """Response with ledger summary and entries."""
    success: bool
    year: Optional[int] = None
    status_filter: Optional[str] = None
    summary: LedgerSummary
    entries: list[dict] = []
    error: Optional[str] = None


class CharitableDonationSummary(BaseModel):
    """Response model for charitable donation summary."""
    tax_year: str | None
    total: float
    tax_deductible_total: float
    by_organization: dict
    by_year: dict


class CharitableSummaryResponse(BaseModel):
    """Response wrapper for charitable summary."""
    success: bool
    data: CharitableDonationSummary | None = None
    error: str | None = None


def _get_default_home_id(current_user: CurrentUserContext) -> str:
    """Get the user's default home ID."""
    if not current_user.default_membership:
        raise HTTPException(status_code=400, detail="No home membership found")
    return current_user.default_membership.home_id


# ---------------------------------------------------------------------------
# Row parsers
# ---------------------------------------------------------------------------

def _pad(row: list, length: int) -> list[str]:
    row = list(row)
    while len(row) < length:
        row.append("")
    return [str(v) for v in row]


def _to_float(val: str) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


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


def _year_matches(date_str: str, year: int) -> bool:
    return date_str.startswith(str(year))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/balance/unreimbursed", response_model=UnreimbursedBalanceResponse)
async def get_unreimbursed_balance(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get total of all unreimbursed HSA expenses.

    Returns zero balance if Google is not connected or the ledger is empty.
    """
    home_id = _get_default_home_id(current_user)

    try:
        cfg = await get_or_create_config(home_id, db, settings)
        creds = build_google_credentials(home_id, db, settings)
        sheets_svc = get_sheets_service(creds)

        rows = read_rows(sheets_svc, cfg["hsa_ledger_id"], "Sheet1")
        unreimbursed = [
            _row_to_hsa_entry(r) for r in rows
            if len(r) > 6 and r[6] == "unreimbursed"
        ]
        return UnreimbursedBalanceResponse(
            total_amount=sum(e["amount"] for e in unreimbursed),
            count=len(unreimbursed),
            is_configured=True,
        )

    except Exception as e:
        logger.error("HSA balance check failed: %s", e, exc_info=True)
        return UnreimbursedBalanceResponse(
            total_amount=0,
            count=0,
            is_configured=False,
        )


@router.get("/summary", response_model=LedgerSummaryResponse)
async def get_ledger_summary(
    year: Optional[int] = Query(None, description="Filter by year (e.g., 2025)"),
    status_filter: Optional[str] = Query(
        None,
        description="Filter by status",
        enum=["reimbursed", "unreimbursed", "not_hsa_eligible"],
    ),
    limit: int = Query(1000, description="Maximum entries to return", ge=1, le=5000),
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get HSA ledger summary with optional filtering."""
    home_id = _get_default_home_id(current_user)

    _empty_summary = LedgerSummary(
        total_entries=0,
        total_amount=0,
        total_reimbursed=0,
        total_unreimbursed=0,
        total_not_eligible=0,
        count_reimbursed=0,
        count_unreimbursed=0,
        count_not_eligible=0,
        available_to_reimburse=0,
    )

    try:
        cfg = await get_or_create_config(home_id, db, settings)
        creds = build_google_credentials(home_id, db, settings)
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

        total_reimbursed = sum(e["amount"] for e in entries if e["status"] == "reimbursed")
        total_unreimbursed = sum(e["amount"] for e in entries if e["status"] == "unreimbursed")
        total_not_eligible = sum(e["amount"] for e in entries if e["status"] == "not_hsa_eligible")

        return LedgerSummaryResponse(
            success=True,
            year=year,
            status_filter=status_filter,
            summary=LedgerSummary(
                total_entries=len(entries),
                total_amount=sum(e["amount"] for e in entries),
                total_reimbursed=total_reimbursed,
                total_unreimbursed=total_unreimbursed,
                total_not_eligible=total_not_eligible,
                count_reimbursed=sum(1 for e in entries if e["status"] == "reimbursed"),
                count_unreimbursed=sum(1 for e in entries if e["status"] == "unreimbursed"),
                count_not_eligible=sum(1 for e in entries if e["status"] == "not_hsa_eligible"),
                available_to_reimburse=total_unreimbursed,
            ),
            entries=entries,
        )

    except Exception as e:
        logger.error("Ledger summary failed: %s", e, exc_info=True)
        return LedgerSummaryResponse(
            success=False,
            year=year,
            status_filter=status_filter,
            summary=_empty_summary,
            error=f"Failed to get ledger summary: {e}",
        )


@router.get("/charitable/summary", response_model=CharitableSummaryResponse)
async def get_charitable_summary(
    tax_year: str | None = None,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get summary of charitable donations by tax year."""
    home_id = _get_default_home_id(current_user)

    try:
        cfg = await get_or_create_config(home_id, db, settings)
        creds = build_google_credentials(home_id, db, settings)
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

        return CharitableSummaryResponse(
            success=True,
            data=CharitableDonationSummary(
                tax_year=tax_year,
                total=total,
                tax_deductible_total=tax_deductible_total,
                by_organization=by_organization,
                by_year=by_year,
            ),
        )

    except Exception as e:
        logger.error("Charitable summary failed: %s", e, exc_info=True)
        return CharitableSummaryResponse(
            success=False,
            error=f"Failed to get charitable summary: {e}",
        )
