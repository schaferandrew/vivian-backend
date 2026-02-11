"""Ledger and balance router."""

import json
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
from vivian_api.services.mcp_client import MCPClient
from vivian_api.services.mcp_registry import get_mcp_server_definitions


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


async def _create_mcp_client(
    mcp_server_id: str,
    db: Session,
    home_id: str,
) -> MCPClient:
    """Create an MCPClient with database-backed configuration."""
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(mcp_server_id)
    if not definition:
        raise ValueError(f"Unknown MCP server: {mcp_server_id}")

    from vivian_api.services.google_integration import build_mcp_env_from_db
    env = await build_mcp_env_from_db(home_id, mcp_server_id, db, settings)
    return MCPClient(
        server_command=definition.command,
        process_env=env,
        server_path_override=definition.server_path,
        mcp_server_id=mcp_server_id,
    )


@router.get("/balance/unreimbursed", response_model=UnreimbursedBalanceResponse)
async def get_unreimbursed_balance(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get total of all unreimbursed HSA expenses.
    
    Returns zero balance if MCP server is not configured or fails.
    """
    home_id = _get_default_home_id(current_user)
    
    try:
        mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
        await mcp_client.start()
        
        try:
            result = await mcp_client.get_unreimbursed_balance()
            
            if "error" in result and not result.get("total_unreimbursed"):
                # MCP server responded with an error (likely spreadsheet not found)
                logger.error("HSA balance MCP error: %s", result)
                return UnreimbursedBalanceResponse(
                    total_amount=0,
                    count=0,
                    is_configured=False
                )
            
            return UnreimbursedBalanceResponse(
                total_amount=result.get("total_unreimbursed", 0),
                count=result.get("count", 0),
                is_configured=True
            )
        finally:
            await mcp_client.stop()
            
    except Exception as e:
        # If MCP server isn't configured or fails to start, return not configured
        logger.error("HSA balance check failed: %s", e, exc_info=True)
        return UnreimbursedBalanceResponse(
            total_amount=0,
            count=0,
            is_configured=False
        )


@router.get("/summary", response_model=LedgerSummaryResponse)
async def get_ledger_summary(
    year: Optional[int] = Query(None, description="Filter by year (e.g., 2025)"),
    status_filter: Optional[str] = Query(None, description="Filter by status", enum=["reimbursed", "unreimbursed", "not_hsa_eligible"]),
    limit: int = Query(1000, description="Maximum entries to return", ge=1, le=5000),
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get HSA ledger summary with optional filtering.
    
    This endpoint answers questions like:
    - "How much have I reimbursed this year?"
    - "How much is available to reimburse?"
    - "What are my total HSA expenses?"
    
    Args:
        year: Optional year to filter entries
        status_filter: Optional status filter (reimbursed, unreimbursed, not_hsa_eligible)
        limit: Maximum number of entries to return (default 1000)
    """
    home_id = _get_default_home_id(current_user)
    
    mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
    await mcp_client.start()
    
    try:
        result = await mcp_client.call_tool(
            "read_ledger_entries",
            {
                "year": year,
                "status_filter": status_filter,
                "limit": limit
            }
        )
        
        # Parse the result
        content = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(content)
        
        if not data.get("success"):
            return LedgerSummaryResponse(
                success=False,
                year=year,
                status_filter=status_filter,
                summary=LedgerSummary(
                    total_entries=0,
                    total_amount=0,
                    total_reimbursed=0,
                    total_unreimbursed=0,
                    total_not_eligible=0,
                    count_reimbursed=0,
                    count_unreimbursed=0,
                    count_not_eligible=0,
                    available_to_reimburse=0,
                ),
                error=data.get("error", "Failed to read ledger")
            )
        
        summary_data = data.get("summary", {})
        
        return LedgerSummaryResponse(
            success=True,
            year=year,
            status_filter=status_filter,
            summary=LedgerSummary(
                total_entries=summary_data.get("total_entries", 0),
                total_amount=summary_data.get("total_amount", 0),
                total_reimbursed=summary_data.get("total_reimbursed", 0),
                total_unreimbursed=summary_data.get("total_unreimbursed", 0),
                total_not_eligible=summary_data.get("total_not_eligible", 0),
                count_reimbursed=summary_data.get("count_reimbursed", 0),
                count_unreimbursed=summary_data.get("count_unreimbursed", 0),
                count_not_eligible=summary_data.get("count_not_eligible", 0),
                available_to_reimburse=summary_data.get("available_to_reimburse", 0),
            ),
            entries=data.get("entries", [])
        )
        
    except Exception as e:
        return LedgerSummaryResponse(
            success=False,
            year=year,
            status_filter=status_filter,
            summary=LedgerSummary(
                total_entries=0,
                total_amount=0,
                total_reimbursed=0,
                total_unreimbursed=0,
                total_not_eligible=0,
                count_reimbursed=0,
                count_unreimbursed=0,
                count_not_eligible=0,
                available_to_reimburse=0,
            ),
            error=f"Failed to get ledger summary: {str(e)}"
        )
    finally:
        await mcp_client.stop()


@router.get("/charitable/summary", response_model=CharitableSummaryResponse)
async def get_charitable_summary(
    tax_year: str | None = None,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get summary of charitable donations by tax year.
    
    Args:
        tax_year: Optional tax year to filter by (e.g., "2025")
    """
    home_id = _get_default_home_id(current_user)
    
    mcp_client = await _create_mcp_client("charitable_ledger", db, home_id)
    await mcp_client.start()
    
    try:
        payload: dict[str, str] = {}
        if tax_year:
            payload["tax_year"] = tax_year
        result = await mcp_client.call_tool(
            "get_charitable_summary",
            payload
        )
        
        # Parse the result
        content = result.get("content", [{}])[0].get("text", "{}")
        data = json.loads(content)
        
        if not data.get("success"):
            return CharitableSummaryResponse(
                success=False,
                error=data.get("error", "Failed to get summary")
            )
        
        return CharitableSummaryResponse(
            success=True,
            data=CharitableDonationSummary(
                tax_year=tax_year,
                total=data.get("total", 0),
                tax_deductible_total=data.get("tax_deductible_total", 0),
                by_organization=data.get("by_organization", {}),
                by_year=data.get("by_year", {}),
            )
        )
        
    except Exception as e:
        return CharitableSummaryResponse(
            success=False,
            error=f"Failed to get charitable summary: {str(e)}"
        )
    finally:
        await mcp_client.stop()
