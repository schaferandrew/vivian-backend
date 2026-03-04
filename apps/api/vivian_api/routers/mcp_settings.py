from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.config import Settings


router = APIRouter(
    prefix="/mcp/settings",
    tags=["mcp"],
    dependencies=[Depends(get_current_user_context)],
)

settings = Settings()


class MCPSettingsResponse(BaseModel):
    mcp_reimbursed_folder_id: str = ""
    mcp_unreimbursed_folder_id: str = ""
    mcp_sheets_spreadsheet_id: str = ""
    charitable_drive_folder_id: str = ""
    charitable_spreadsheet_id: str = ""
    charitable_worksheet_name: str = ""


class MCPSettingsRequest(BaseModel):
    mcp_reimbursed_folder_id: str = ""
    mcp_unreimbursed_folder_id: str = ""
    mcp_sheets_spreadsheet_id: str = ""
    charitable_drive_folder_id: str = ""
    charitable_spreadsheet_id: str = ""
    charitable_worksheet_name: str = ""


@router.get("", response_model=MCPSettingsResponse)
async def get_mcp_settings() -> MCPSettingsResponse:
    """Get MCP folder and sheet settings."""
    return MCPSettingsResponse(
        mcp_reimbursed_folder_id=settings.mcp_reimbursed_folder_id,
        mcp_unreimbursed_folder_id=settings.mcp_unreimbursed_folder_id,
        mcp_sheets_spreadsheet_id=settings.mcp_sheets_spreadsheet_id,
        charitable_drive_folder_id=settings.charitable_drive_folder_id,
        charitable_spreadsheet_id=settings.charitable_spreadsheet_id,
        charitable_worksheet_name=settings.charitable_worksheet_name,
    )


@router.post("")
async def save_mcp_settings(request: MCPSettingsRequest) -> MCPSettingsResponse:
    """Save MCP folder and sheet settings (env vars must be set for persistence)."""
    settings.mcp_reimbursed_folder_id = request.mcp_reimbursed_folder_id
    settings.mcp_unreimbursed_folder_id = request.mcp_unreimbursed_folder_id
    settings.mcp_sheets_spreadsheet_id = request.mcp_sheets_spreadsheet_id
    settings.charitable_drive_folder_id = request.charitable_drive_folder_id
    settings.charitable_spreadsheet_id = request.charitable_spreadsheet_id
    settings.charitable_worksheet_name = request.charitable_worksheet_name

    return MCPSettingsResponse(
        mcp_reimbursed_folder_id=settings.mcp_reimbursed_folder_id,
        mcp_unreimbursed_folder_id=settings.mcp_unreimbursed_folder_id,
        mcp_sheets_spreadsheet_id=settings.mcp_sheets_spreadsheet_id,
        charitable_drive_folder_id=settings.charitable_drive_folder_id,
        charitable_spreadsheet_id=settings.charitable_spreadsheet_id,
        charitable_worksheet_name=settings.charitable_worksheet_name,
    )
