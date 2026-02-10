"""Ledger and balance router."""

from fastapi import APIRouter, Depends, HTTPException

from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.models.schemas import UnreimbursedBalanceResponse
from vivian_api.services.mcp_client import MCPClient


router = APIRouter(
    prefix="/ledger",
    tags=["ledger"],
    dependencies=[Depends(get_current_user_context)],
)


@router.get("/balance/unreimbursed", response_model=UnreimbursedBalanceResponse)
async def get_unreimbursed_balance():
    """Get total of all unreimbursed HSA expenses."""
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
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
