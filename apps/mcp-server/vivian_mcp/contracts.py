"""Typed MCP tool contracts for Vivian MCP server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ReimbursementStatus = Literal["reimbursed", "unreimbursed", "not_hsa_eligible"]
FilterOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "starts_with",
    "ends_with",
    "in",
    "gt",
    "gte",
    "lt",
    "lte",
]


class ToolInputModel(BaseModel):
    """Base input model for MCP tool contracts."""

    model_config = ConfigDict(extra="forbid")


class ToolOutputModel(BaseModel):
    """Base output model for MCP tool contracts.

    Output models allow additive fields so existing behavior remains compatible.
    """

    model_config = ConfigDict(extra="allow")


class EmptyInput(ToolInputModel):
    """No-argument tool input."""


class ColumnFilter(ToolInputModel):
    column: str
    operator: FilterOperator = "equals"
    value: Any
    case_sensitive: bool = False


class ParseReceiptInput(ToolInputModel):
    pdf_path: str


class ParseReceiptOutput(ToolOutputModel):
    status: str
    pdf_path: str
    message: str


class AppendExpenseInput(ToolInputModel):
    expense_json: dict[str, Any]
    reimbursement_status: ReimbursementStatus
    drive_file_id: str
    check_duplicates: bool = True
    force_append: bool = False


class AppendExpenseOutput(ToolOutputModel):
    success: bool
    entry_appended: bool
    entry_id: str | None = None
    updated_range: str | None = None
    duplicate_check: dict[str, Any] | None = None
    error: str | None = None


class DuplicateMatch(ToolOutputModel):
    entry_id: str = ""
    provider: str = ""
    date: str = ""
    paid_date: str = ""
    amount: float = 0.0
    hsa_eligible: bool = True
    status: str = ""
    reimbursement_date: str = ""
    drive_file_id: str = ""
    confidence: float = 0.0
    match_type: str | None = None
    days_difference: int | None = None
    message: str | None = None


class CheckDuplicatesInput(ToolInputModel):
    expense_json: dict[str, Any]
    fuzzy_days: int = 3


class CheckDuplicatesOutput(ToolOutputModel):
    is_duplicate: bool
    potential_duplicates: list[DuplicateMatch] = Field(default_factory=list)
    recommendation: str = "import"
    total_duplicates_found: int | None = None
    success: bool | None = None
    error: str | None = None


class UpdateExpenseStatusInput(ToolInputModel):
    expense_id: str
    new_status: ReimbursementStatus
    reimbursement_date: str | None = None


class UpdateExpenseStatusOutput(ToolOutputModel):
    success: bool
    expense_id: str | None = None
    new_status: str | None = None
    error: str | None = None


class GetUnreimbursedBalanceOutput(ToolOutputModel):
    total_unreimbursed: float
    count: int
    success: bool | None = None
    error: str | None = None


class HsaLedgerEntry(ToolOutputModel):
    id: str = ""
    provider: str = ""
    service_date: str = ""
    paid_date: str = ""
    amount: float = 0.0
    hsa_eligible: str | bool = True
    status: str = "unreimbursed"
    reimbursement_date: str = ""
    drive_file_id: str = ""
    confidence: str | float = "0.9"
    created_at: str = ""


class HsaLedgerSummary(ToolOutputModel):
    total_entries: int = 0
    total_amount: float = 0.0
    total_reimbursed: float = 0.0
    total_unreimbursed: float = 0.0
    total_not_eligible: float = 0.0
    count_reimbursed: int = 0
    count_unreimbursed: int = 0
    count_not_eligible: int = 0
    available_to_reimburse: float = 0.0


class ReadLedgerEntriesInput(ToolInputModel):
    year: int | None = None
    status_filter: ReimbursementStatus | None = None
    limit: int = 1000
    column_filters: list[ColumnFilter] | None = None


class ReadLedgerEntriesOutput(ToolOutputModel):
    success: bool
    entries: list[HsaLedgerEntry] = Field(default_factory=list)
    summary: HsaLedgerSummary = Field(default_factory=HsaLedgerSummary)
    error: str | None = None


class BulkImportFromDirectoryInput(ToolInputModel):
    directory_path: str
    reimbursement_status_override: ReimbursementStatus | None = None


class BulkImportFromDirectoryOutput(ToolOutputModel):
    total_files: int = 0
    directory: str = ""
    message: str = ""
    files: list[str] = Field(default_factory=list)
    success: bool | None = None
    error: str | None = None


class BulkImportReceiptItem(ToolInputModel):
    local_file_path: str
    expense_json: dict[str, Any]
    reimbursement_status: ReimbursementStatus
    filename: str | None = None


class BulkImportReceiptsInput(ToolInputModel):
    receipts: list[BulkImportReceiptItem]
    check_duplicates: bool = True
    force_append: bool = False
    fuzzy_days: int = 3


class BulkImportReceiptsOutput(ToolOutputModel):
    success: bool
    imported_count: int
    failed_count: int
    total_amount: float
    results: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class UploadReceiptInput(ToolInputModel):
    local_file_path: str
    status: ReimbursementStatus
    filename: str | None = None


class UploadReceiptOutput(ToolOutputModel):
    success: bool
    file_id: str | None = None
    filename: str | None = None
    web_view_link: str | None = None
    folder: str | None = None
    error: str | None = None


class UploadCharitableReceiptInput(ToolInputModel):
    local_file_path: str
    tax_year: str | None = None
    filename: str | None = None


class UploadCharitableReceiptOutput(ToolOutputModel):
    success: bool
    file_id: str | None = None
    filename: str | None = None
    web_view_link: str | None = None
    folder_id: str | None = None
    tax_year: str | None = None
    error: str | None = None


class AppendCharitableDonationInput(ToolInputModel):
    donation_json: dict[str, Any]
    drive_file_id: str
    check_duplicates: bool = True
    force_append: bool = False


class AppendCharitableDonationOutput(ToolOutputModel):
    success: bool
    entry_id: str | None = None
    tax_year: str | None = None
    duplicate_check: dict[str, Any] | None = None
    error: str | None = None


class AppendCashCharitableDonationInput(ToolInputModel):
    donation_json: dict[str, Any]
    check_duplicates: bool = True
    force_append: bool = False


class AppendCashCharitableDonationOutput(ToolOutputModel):
    success: bool
    entry_id: str | None = None
    tax_year: str | None = None
    duplicate_check: dict[str, Any] | None = None
    error: str | None = None

class CharitableDuplicateMatch(ToolOutputModel):
    organization: str = ""
    date: str = ""
    amount: float = 0.0
    match_type: str = "exact"
    days_difference: int | None = None


class CheckCharitableDuplicatesInput(ToolInputModel):
    donation_json: dict[str, Any]
    fuzzy_days: int = 3


class CheckCharitableDuplicatesOutput(ToolOutputModel):
    is_duplicate: bool
    potential_duplicates: list[CharitableDuplicateMatch] = Field(default_factory=list)
    recommendation: str = "import"
    check_error: str | None = None


class GetCharitableSummaryInput(ToolInputModel):
    tax_year: str | int | None = None
    column_filters: list[ColumnFilter] | None = None


class CharitableSummaryTotals(ToolOutputModel):
    total: float = 0.0
    count: int = 0


class CharitableSummaryOutput(ToolOutputModel):
    success: bool
    tax_year: str | int | None = None
    total: float = 0.0
    tax_deductible_total: float = 0.0
    by_organization: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)
    by_year: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)
    error: str | None = None


class ReadCharitableLedgerEntriesInput(ToolInputModel):
    tax_year: str | int | None = None
    organization: str | None = None
    tax_deductible: bool | None = None
    limit: int = 1000
    column_filters: list[ColumnFilter] | None = None


class CharitableLedgerEntry(ToolOutputModel):
    id: str = ""
    organization_name: str = ""
    donation_date: str = ""
    amount: float = 0.0
    tax_deductible: bool = True
    description: str = ""
    drive_file_id: str = ""
    tax_year: str = ""
    confidence: str | float = "0.9"
    created_at: str = ""


class ReadCharitableLedgerEntriesSummary(ToolOutputModel):
    total_entries: int = 0
    total_amount: float = 0.0
    tax_deductible_total: float = 0.0
    non_deductible_total: float = 0.0
    count_tax_deductible: int = 0
    count_non_deductible: int = 0
    by_organization: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)
    by_year: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)


class ReadCharitableLedgerEntriesOutput(ToolOutputModel):
    success: bool
    tax_year: str | int | None = None
    entries: list[CharitableLedgerEntry] = Field(default_factory=list)
    summary: ReadCharitableLedgerEntriesSummary = Field(
        default_factory=ReadCharitableLedgerEntriesSummary
    )
    total: float = 0.0
    tax_deductible_total: float = 0.0
    by_organization: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)
    by_year: dict[str, CharitableSummaryTotals] = Field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class MCPToolContract:
    """Single MCP tool contract entry."""

    name: str
    description: str
    input_model: type[ToolInputModel]
    output_model: type[ToolOutputModel]
    server_id: str | None = None
    model_visible: bool = False

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


TOOL_CONTRACTS: tuple[MCPToolContract, ...] = (
    MCPToolContract(
        name="parse_receipt_to_expense_schema",
        description="Parse a receipt PDF and extract structured expense data",
        input_model=ParseReceiptInput,
        output_model=ParseReceiptOutput,
    ),
    MCPToolContract(
        name="append_expense_to_ledger",
        description="Add an expense to the Google Sheets ledger",
        input_model=AppendExpenseInput,
        output_model=AppendExpenseOutput,
    ),
    MCPToolContract(
        name="check_for_duplicates",
        description="Check if an expense is a duplicate of existing entries in the ledger",
        input_model=CheckDuplicatesInput,
        output_model=CheckDuplicatesOutput,
    ),
    MCPToolContract(
        name="update_expense_status",
        description="Update the reimbursement status of an existing expense",
        input_model=UpdateExpenseStatusInput,
        output_model=UpdateExpenseStatusOutput,
    ),
    MCPToolContract(
        name="get_unreimbursed_balance",
        description="Get total of all unreimbursed expenses",
        input_model=EmptyInput,
        output_model=GetUnreimbursedBalanceOutput,
        server_id="hsa_ledger",
        model_visible=True,
    ),
    MCPToolContract(
        name="read_ledger_entries",
        description="Read HSA ledger entries with optional filtering by year, status, and column predicates",
        input_model=ReadLedgerEntriesInput,
        output_model=ReadLedgerEntriesOutput,
        server_id="hsa_ledger",
        model_visible=True,
    ),
    MCPToolContract(
        name="bulk_import_receipts_from_directory",
        description="Bulk import all PDF receipts from a directory",
        input_model=BulkImportFromDirectoryInput,
        output_model=BulkImportFromDirectoryOutput,
    ),
    MCPToolContract(
        name="bulk_import_receipts",
        description="Bulk import parsed receipts: upload files and batch append ledger rows",
        input_model=BulkImportReceiptsInput,
        output_model=BulkImportReceiptsOutput,
    ),
    MCPToolContract(
        name="upload_receipt_to_drive",
        description="Upload a receipt PDF to Google Drive in the appropriate folder",
        input_model=UploadReceiptInput,
        output_model=UploadReceiptOutput,
    ),
    MCPToolContract(
        name="upload_charitable_receipt_to_drive",
        description="Upload a charitable donation receipt to Google Drive organized by tax year",
        input_model=UploadCharitableReceiptInput,
        output_model=UploadCharitableReceiptOutput,
    ),
    MCPToolContract(
        name="append_charitable_donation_to_ledger",
        description="Add a charitable donation to the Google Sheets ledger",
        input_model=AppendCharitableDonationInput,
        output_model=AppendCharitableDonationOutput,
    ),

    MCPToolContract(
        name="append_cash_charitable_donation_to_ledger",
        description="Add a cash charitable donation directly to the Google Sheets ledger without a receipt upload",
        input_model=AppendCashCharitableDonationInput,
        output_model=AppendCashCharitableDonationOutput,
    ),
    MCPToolContract(
        name="check_charitable_duplicates",
        description="Check if a charitable donation is a duplicate of existing entries",
        input_model=CheckCharitableDuplicatesInput,
        output_model=CheckCharitableDuplicatesOutput,
    ),
    MCPToolContract(
        name="get_charitable_summary",
        description="Get summary of charitable donations by tax year with optional column predicates",
        input_model=GetCharitableSummaryInput,
        output_model=CharitableSummaryOutput,
        server_id="charitable_ledger",
        model_visible=True,
    ),
    MCPToolContract(
        name="read_charitable_ledger_entries",
        description=(
            "Read charitable ledger entries with optional tax year, organization, tax-deductible, "
            "and column predicate filters"
        ),
        input_model=ReadCharitableLedgerEntriesInput,
        output_model=ReadCharitableLedgerEntriesOutput,
        server_id="charitable_ledger",
        model_visible=True,
    ),
)

TOOL_CONTRACTS_BY_NAME: dict[str, MCPToolContract] = {
    contract.name: contract for contract in TOOL_CONTRACTS
}


def get_tool_contract(name: str) -> MCPToolContract | None:
    """Return contract for a tool name, if present."""

    return TOOL_CONTRACTS_BY_NAME.get(name)


def validate_tool_input(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Validate/normalize tool input against a contract."""

    contract = TOOL_CONTRACTS_BY_NAME.get(name)
    if not contract:
        return arguments or {}
    model = contract.input_model.model_validate(arguments or {})
    return model.model_dump(exclude_none=True)


def validate_tool_output(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate/normalize tool output against a contract."""

    contract = TOOL_CONTRACTS_BY_NAME.get(name)
    if not contract:
        return payload
    model = contract.output_model.model_validate(payload)
    return model.model_dump(exclude_none=True)


def build_model_tool_specs() -> dict[str, dict[str, Any]]:
    """Build API model-tool mapping from MCP contracts."""

    specs: dict[str, dict[str, Any]] = {}
    for contract in TOOL_CONTRACTS:
        if not contract.model_visible or not contract.server_id:
            continue
        specs[contract.name] = {
            "server_id": contract.server_id,
            "description": contract.description,
            "parameters": contract.input_schema(),
        }
    return specs
