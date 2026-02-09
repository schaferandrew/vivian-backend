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
    is_duplicate: bool = Field(default=False, description="Whether parsed receipt appears to be a duplicate")
    duplicate_info: Optional[list["DuplicateInfo"]] = Field(default=None, description="Potential duplicate matches")
    duplicate_check_error: Optional[str] = Field(default=None, description="Duplicate check warning if unavailable")


class ReceiptParseRequest(BaseModel):
    """Request to parse a previously uploaded receipt."""
    temp_file_path: str


class CheckDuplicateRequest(BaseModel):
    """Request to check one expense payload for duplicates."""
    expense_data: ExpenseSchema
    fuzzy_days: int = 3


class CheckDuplicateResponse(BaseModel):
    """Response from duplicate-check endpoint."""
    is_duplicate: bool
    duplicate_info: list["DuplicateInfo"] = Field(default_factory=list)
    recommendation: str = "import"
    check_error: Optional[str] = None


class ConfirmReceiptRequest(BaseModel):
    """Request to confirm and save a parsed receipt."""
    temp_file_path: str
    expense_data: ExpenseSchema
    status: ReimbursementStatus
    reimbursement_date: Optional[date] = None
    notes: Optional[str] = None
    force: bool = Field(default=False, description="Force import even if duplicates detected")


class ConfirmReceiptResponse(BaseModel):
    """Response from confirm receipt endpoint."""
    success: bool
    ledger_entry_id: Optional[str] = None
    drive_file_id: Optional[str] = None
    message: str
    is_duplicate: bool = Field(default=False, description="Whether this receipt is a duplicate")
    duplicate_info: Optional[list["DuplicateInfo"]] = Field(default=None, description="Details of potential duplicates")


class DuplicateInfo(BaseModel):
    """Information about a potential duplicate entry."""
    entry_id: str
    provider: str
    service_date: Optional[str] = None
    paid_date: Optional[str] = None
    amount: float
    hsa_eligible: bool = True
    status: str
    reimbursement_date: Optional[str] = None
    drive_file_id: Optional[str] = None
    confidence: float = 0
    match_type: str = Field(..., description="Type of match: 'exact' or 'fuzzy_date'")
    days_difference: Optional[int] = Field(None, description="Days difference for fuzzy matches")
    message: Optional[str] = Field(None, description="Human-readable duplicate match reason")


class BulkImportFileStatus(str):
    """Status of a file in bulk import."""
    NEW = "new"
    DUPLICATE_EXACT = "duplicate_exact"
    DUPLICATE_FUZZY = "duplicate_fuzzy"
    FLAGGED = "flagged"
    FAILED = "failed"
    SKIPPED = "skipped"


class BulkImportFileResult(BaseModel):
    """Result for a single file in bulk import."""
    filename: str
    status: str = Field(..., description="Status: new, duplicate_exact, duplicate_fuzzy, flagged, failed, skipped")
    temp_file_path: Optional[str] = None
    expense: Optional[ExpenseSchema] = None
    confidence: float = 0
    duplicate_info: Optional[list[DuplicateInfo]] = None
    error: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class BulkImportSummary(BaseModel):
    """Summary of bulk import operation."""
    total_amount: float = 0
    new_count: int = 0
    duplicate_count: int = 0
    flagged_count: int = 0
    failed_count: int = 0
    ready_to_import: int = 0


class BulkImportRequest(BaseModel):
    """Request for bulk import."""
    directory_path: str
    status_override: Optional[ReimbursementStatus] = None
    skip_errors: bool = True
    check_duplicates: bool = True
    duplicate_action: str = Field(default="flag", description="Action for duplicates: skip, flag, or ask")


class BulkImportTempScanRequest(BaseModel):
    """Request for bulk import scan from uploaded temp files."""
    temp_file_paths: list[str] = Field(..., description="List of uploaded temp file paths")
    status_override: Optional[ReimbursementStatus] = None
    skip_errors: bool = True
    check_duplicates: bool = True
    duplicate_action: str = Field(default="flag", description="Action for duplicates: skip, flag, or ask")


class BulkImportResponse(BaseModel):
    """Response from bulk import endpoint."""
    total_files: int
    mode: str = Field(..., description="Mode: scan or import")
    new: list[BulkImportFileResult] = Field(default_factory=list)
    duplicates: list[BulkImportFileResult] = Field(default_factory=list)
    flagged: list[BulkImportFileResult] = Field(default_factory=list)
    failed: list[BulkImportFileResult] = Field(default_factory=list)
    summary: BulkImportSummary


class BulkImportConfirmRequest(BaseModel):
    """Request to confirm bulk import after review."""
    items: list["BulkImportConfirmItem"] = Field(default_factory=list, description="Parsed receipts selected for import")
    temp_file_paths: list[str] = Field(default_factory=list, description="Legacy list of temp file paths to import")
    status_override: Optional[ReimbursementStatus] = None
    force: bool = Field(default=False, description="Force import even if duplicates are detected")


class BulkImportConfirmItem(BaseModel):
    """Single parsed item selected for bulk import."""
    temp_file_path: str
    expense_data: ExpenseSchema
    status: Optional[ReimbursementStatus] = None


class BulkImportConfirmResponse(BaseModel):
    """Response from bulk import confirmation."""
    success: bool
    imported_count: int
    failed_count: int
    total_amount: float
    results: list[BulkImportFileResult]
    message: str


class UnreimbursedBalanceResponse(BaseModel):
    """Response with unreimbursed balance."""
    total_amount: float
    count: int


class HealthCheckResponse(BaseModel):
    """Health check response."""
    status: str
    version: str = "0.1.0"
