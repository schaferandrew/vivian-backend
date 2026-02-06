"""Pydantic models for API."""

from datetime import date
from typing import Optional
from pydantic import BaseModel, Field

from vivian_shared.models import ExpenseSchema, ParsedReceipt, ReimbursementStatus


class ReceiptUploadResponse(BaseModel):
    """Response from receipt upload endpoint."""
    temp_file_path: str
    message: str = "Receipt uploaded successfully"


class ReceiptParseResponse(BaseModel):
    """Response from receipt parse endpoint."""
    parsed_data: ParsedReceipt
    needs_review: bool = Field(description="Whether human review is needed")
    temp_file_path: str


class ConfirmReceiptRequest(BaseModel):
    """Request to confirm and save a parsed receipt."""
    temp_file_path: str
    expense_data: ExpenseSchema
    status: ReimbursementStatus
    reimbursement_date: Optional[date] = None
    notes: Optional[str] = None


class ConfirmReceiptResponse(BaseModel):
    """Response from confirm receipt endpoint."""
    success: bool
    ledger_entry_id: str
    drive_file_id: str
    message: str


class BulkImportRequest(BaseModel):
    """Request for bulk import."""
    directory_path: str
    status_override: Optional[ReimbursementStatus] = None
    skip_errors: bool = True


class BulkImportFileResult(BaseModel):
    """Result for a single file in bulk import."""
    filename: str
    success: bool
    expense: Optional[ExpenseSchema] = None
    error: Optional[str] = None


class BulkImportResponse(BaseModel):
    """Response from bulk import endpoint."""
    total_files: int
    successful: int
    failed: int
    results: list[BulkImportFileResult]


class UnreimbursedBalanceResponse(BaseModel):
    """Response with unreimbursed balance."""
    total_amount: float
    count: int


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str
    version: str = "0.1.0"
