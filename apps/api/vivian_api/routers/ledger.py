"""Ledger and balance router."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import (
    CurrentUserContext,
    get_current_user_context,
)
from vivian_api.db.database import get_db
from vivian_api.models.schemas import UnreimbursedBalanceResponse
from vivian_api.services.mcp_client import MCPClient


router = APIRouter(
    prefix="/ledger",
    tags=["ledger"],
    dependencies=[Depends(get_current_user_context)],
)


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
    return str(current_user.default_membership.home_id)


@router.get("/balance/unreimbursed", response_model=UnreimbursedBalanceResponse)
async def get_unreimbursed_balance(
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Get total of all unreimbursed HSA expenses."""
    home_id = _get_default_home_id(current_user)
    
    # Create MCP client with database-backed configuration
    mcp_client = await MCPClient.from_db(
        server_command=["python", "-m", "vivian_mcp.server"],
        home_id=home_id,
        mcp_server_id="vivian_hsa",
        db=db,
    )
    await mcp_client.start()
    
    try:
        result = await mcp_client.get_unreimbursed_balance()
        
        if not result.get("success") and "error" in result:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get balance: {result.get('error')}"
            )
        
        return UnreimbursedBalanceResponse(
            total_amount=result.get("total_unreimbursed", 0),
            count=result.get("count", 0)
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get balance: {str(e)}")
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
    
    # Create MCP client with database-backed configuration
    mcp_client = await MCPClient.from_db(
        server_command=["python", "-m", "vivian_mcp.server"],
        home_id=home_id,
        mcp_server_id="vivian_charitable",
        db=db,
    )
    await mcp_client.start()
    
    try:
        result = await mcp_client.call_tool(
            "get_charitable_summary",
            {"tax_year": tax_year}
        )
        
        # Parse the result
        content = result.get("content", [{}])[0].get("text", "{}")
        import json
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
