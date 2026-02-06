"""Shared models for Vivian household agent."""

from datetime import date, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ReimbursementStatus(str, Enum):
    """Expense reimbursement status."""
    REIMBURSED = "reimbursed"
    UNREIMBURSED = "unreimbursed"
    NOT_HSA_ELIGIBLE = "not_hsa_eligible"


class ExpenseSchema(BaseModel):
    """Structured expense data extracted from receipt."""
    provider: str = Field(description="Medical provider name")
    service_date: Optional[date] = Field(None, description="Date service was provided")
    paid_date: Optional[date] = Field(None, description="Date payment was made")
    amount: float = Field(description="Total amount paid")
    hsa_eligible: bool = Field(default=True, description="Whether expense is HSA eligible")
    raw_model_output: Optional[str] = Field(None, description="Raw model output for debugging")


class ParsedReceipt(BaseModel):
    """Result of parsing a receipt."""
    expense: ExpenseSchema
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence score")
    parsing_errors: list[str] = Field(default_factory=list)


class LedgerEntry(BaseModel):
    """Single entry in the HSA expense ledger."""
    id: str = Field(description="Unique entry ID")
    provider: str
    service_date: Optional[date] = None
    paid_date: Optional[date] = None
    amount: float
    hsa_eligible: bool = True
    status: ReimbursementStatus = ReimbursementStatus.UNREIMBURSED
    reimbursement_date: Optional[date] = None
    drive_file_id: str = Field(description="Google Drive file ID")
    confidence: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BulkImportConfig(BaseModel):
    """Configuration for bulk import operation."""
    directory_path: str = Field(description="Path to directory containing PDFs")
    status_override: Optional[ReimbursementStatus] = Field(
        None, description="Override reimbursement status for all receipts"
    )
    skip_errors: bool = Field(default=True, description="Continue on parse errors")


class BulkImportResult(BaseModel):
    """Result of bulk import operation."""
    total_files: int
    successful: int
    failed: int
    entries: list[LedgerEntry] = Field(default_factory=list)
    errors: list[tuple[str, str]] = Field(default_factory=list)  # (filename, error)


class HumanConfirmation(BaseModel):
    """User confirmation/edits after parsing."""
    confirmed: bool = Field(description="User confirmed the data is correct")
    edited_expense: Optional[ExpenseSchema] = Field(None, description="User-edited expense data")
    selected_status: ReimbursementStatus
    notes: Optional[str] = None
