"""Vivian MCP Server - Household agent tools (FastMCP)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, TypeVar

from mcp.server.fastmcp import FastMCP

from vivian_mcp.config import Settings
from vivian_mcp.contracts import (
    AppendCashCharitableDonationOutput,
    AppendCharitableDonationOutput,
    AppendExpenseOutput,
    BulkImportFromDirectoryOutput,
    BulkImportReceiptItem,
    BulkImportReceiptsOutput,
    CharitableSummaryOutput,
    CheckCharitableDuplicatesOutput,
    CheckDuplicatesOutput,
    ColumnFilter,
    GetUnreimbursedBalanceOutput,
    ParseReceiptOutput,
    ReadCharitableLedgerEntriesOutput,
    ReadLedgerEntriesOutput,
    ReimbursementStatus,
    TOOL_CONTRACTS_BY_NAME,
    ToolOutputModel,
    UpdateExpenseStatusOutput,
    UploadCharitableReceiptOutput,
    UploadReceiptOutput,
    validate_tool_input,
    validate_tool_output,
)
from vivian_mcp.tools.charitable_tools import CharitableToolManager
from vivian_mcp.tools.drive_tools import DriveToolManager
from vivian_mcp.tools.hsa_tools import HSAToolManager


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Settings]:
    """Manage application lifecycle."""
    settings = Settings()
    yield settings


# Create MCP server
app = FastMCP("vivian-mcp", lifespan=app_lifespan)

# Initialize tool managers
hsa_tools = HSAToolManager()
drive_tools = DriveToolManager()
charitable_tools = CharitableToolManager()


OutputT = TypeVar("OutputT", bound=ToolOutputModel)


def _contract_description(name: str) -> str:
    """Return the configured contract description for a tool."""
    contract = TOOL_CONTRACTS_BY_NAME.get(name)
    if not contract:
        raise ValueError(f"Unknown tool contract: {name}")
    return contract.description


def _parse_manager_payload(raw_result: Any) -> dict[str, Any]:
    """Normalize manager return values to dictionary payloads."""
    if isinstance(raw_result, dict):
        return raw_result

    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
            if isinstance(parsed, dict):
                return parsed
            return {"success": True, "value": parsed}
        except Exception:
            return {"success": False, "error": raw_result}

    return {
        "success": False,
        "error": f"Unsupported tool response type: {type(raw_result).__name__}",
    }


async def _execute_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one validated tool call to the appropriate manager."""
    if name == "parse_receipt_to_expense_schema":
        raw_result = await hsa_tools.parse_receipt(arguments["pdf_path"])
    elif name == "append_expense_to_ledger":
        raw_result = await hsa_tools.append_to_ledger(
            arguments["expense_json"],
            arguments["reimbursement_status"],
            arguments["drive_file_id"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
        )
    elif name == "check_for_duplicates":
        raw_result = await hsa_tools.check_for_duplicates(
            arguments["expense_json"],
            arguments.get("fuzzy_days", 3),
        )
    elif name == "update_expense_status":
        raw_result = await hsa_tools.update_status(
            arguments["expense_id"],
            arguments["new_status"],
            arguments.get("reimbursement_date"),
        )
    elif name == "get_unreimbursed_balance":
        raw_result = await hsa_tools.get_unreimbursed_balance()
    elif name == "read_ledger_entries":
        raw_result = await hsa_tools.read_ledger_entries(
            year=arguments.get("year"),
            status_filter=arguments.get("status_filter"),
            limit=arguments.get("limit", 1000),
            column_filters=arguments.get("column_filters"),
        )
    elif name == "bulk_import_receipts_from_directory":
        raw_result = await hsa_tools.bulk_import(
            arguments["directory_path"],
            arguments.get("reimbursement_status_override"),
        )
    elif name == "bulk_import_receipts":
        raw_result = await hsa_tools.bulk_import_receipts(
            arguments["receipts"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
            arguments.get("fuzzy_days", 3),
        )
    elif name == "upload_receipt_to_drive":
        raw_result = await drive_tools.upload_receipt(
            arguments["local_file_path"],
            arguments["status"],
            arguments.get("filename"),
        )
    elif name == "upload_charitable_receipt_to_drive":
        raw_result = await charitable_tools.upload_receipt_to_drive(
            arguments["local_file_path"],
            arguments.get("tax_year"),
            arguments.get("filename"),
        )
    elif name == "append_charitable_donation_to_ledger":
        raw_result = await charitable_tools.append_donation_to_ledger(
            arguments["donation_json"],
            arguments["drive_file_id"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
        )
    elif name == "append_cash_charitable_donation_to_ledger":
        raw_result = await charitable_tools.append_cash_donation_to_ledger(
            arguments["donation_json"],
            arguments.get("check_duplicates", True),
            arguments.get("force_append", False),
        )
    elif name == "check_charitable_duplicates":
        raw_result = await charitable_tools.check_for_duplicates(
            arguments["donation_json"],
            arguments.get("fuzzy_days", 3),
        )
    elif name == "get_charitable_summary":
        raw_result = await charitable_tools.get_donation_summary(
            arguments.get("tax_year"),
            arguments.get("column_filters"),
        )
    elif name == "read_charitable_ledger_entries":
        raw_result = await charitable_tools.read_donation_entries(
            tax_year=arguments.get("tax_year"),
            organization=arguments.get("organization"),
            tax_deductible=arguments.get("tax_deductible"),
            limit=arguments.get("limit", 1000),
            column_filters=arguments.get("column_filters"),
        )
    else:
        raise ValueError(f"Unknown tool: {name}")

    return _parse_manager_payload(raw_result)


async def _run_tool(name: str, output_model: type[OutputT], **arguments: Any) -> OutputT:
    """Validate inputs/outputs around manager execution and return typed output."""
    validated_arguments = validate_tool_input(name, arguments)
    payload = await _execute_tool(name, validated_arguments)
    validated_payload = validate_tool_output(name, payload)
    return output_model.model_validate(validated_payload)


@app.tool(
    name="parse_receipt_to_expense_schema",
    description=_contract_description("parse_receipt_to_expense_schema"),
)
async def parse_receipt_to_expense_schema(pdf_path: str) -> ParseReceiptOutput:
    return await _run_tool("parse_receipt_to_expense_schema", ParseReceiptOutput, pdf_path=pdf_path)


@app.tool(
    name="append_expense_to_ledger",
    description=_contract_description("append_expense_to_ledger"),
)
async def append_expense_to_ledger(
    expense_json: dict[str, Any],
    reimbursement_status: ReimbursementStatus,
    drive_file_id: str,
    check_duplicates: bool = True,
    force_append: bool = False,
) -> AppendExpenseOutput:
    return await _run_tool(
        "append_expense_to_ledger",
        AppendExpenseOutput,
        expense_json=expense_json,
        reimbursement_status=reimbursement_status,
        drive_file_id=drive_file_id,
        check_duplicates=check_duplicates,
        force_append=force_append,
    )


@app.tool(name="check_for_duplicates", description=_contract_description("check_for_duplicates"))
async def check_for_duplicates(
    expense_json: dict[str, Any],
    fuzzy_days: int = 3,
) -> CheckDuplicatesOutput:
    return await _run_tool(
        "check_for_duplicates",
        CheckDuplicatesOutput,
        expense_json=expense_json,
        fuzzy_days=fuzzy_days,
    )


@app.tool(name="update_expense_status", description=_contract_description("update_expense_status"))
async def update_expense_status(
    expense_id: str,
    new_status: ReimbursementStatus,
    reimbursement_date: str | None = None,
) -> UpdateExpenseStatusOutput:
    return await _run_tool(
        "update_expense_status",
        UpdateExpenseStatusOutput,
        expense_id=expense_id,
        new_status=new_status,
        reimbursement_date=reimbursement_date,
    )


@app.tool(
    name="get_unreimbursed_balance",
    description=_contract_description("get_unreimbursed_balance"),
)
async def get_unreimbursed_balance() -> GetUnreimbursedBalanceOutput:
    return await _run_tool("get_unreimbursed_balance", GetUnreimbursedBalanceOutput)


@app.tool(name="read_ledger_entries", description=_contract_description("read_ledger_entries"))
async def read_ledger_entries(
    year: int | None = None,
    status_filter: ReimbursementStatus | None = None,
    limit: int = 1000,
    column_filters: list[ColumnFilter] | None = None,
) -> ReadLedgerEntriesOutput:
    return await _run_tool(
        "read_ledger_entries",
        ReadLedgerEntriesOutput,
        year=year,
        status_filter=status_filter,
        limit=limit,
        column_filters=column_filters,
    )


@app.tool(
    name="bulk_import_receipts_from_directory",
    description=_contract_description("bulk_import_receipts_from_directory"),
)
async def bulk_import_receipts_from_directory(
    directory_path: str,
    reimbursement_status_override: ReimbursementStatus | None = None,
) -> BulkImportFromDirectoryOutput:
    return await _run_tool(
        "bulk_import_receipts_from_directory",
        BulkImportFromDirectoryOutput,
        directory_path=directory_path,
        reimbursement_status_override=reimbursement_status_override,
    )


@app.tool(name="bulk_import_receipts", description=_contract_description("bulk_import_receipts"))
async def bulk_import_receipts(
    receipts: list[BulkImportReceiptItem],
    check_duplicates: bool = True,
    force_append: bool = False,
    fuzzy_days: int = 3,
) -> BulkImportReceiptsOutput:
    return await _run_tool(
        "bulk_import_receipts",
        BulkImportReceiptsOutput,
        receipts=receipts,
        check_duplicates=check_duplicates,
        force_append=force_append,
        fuzzy_days=fuzzy_days,
    )


@app.tool(name="upload_receipt_to_drive", description=_contract_description("upload_receipt_to_drive"))
async def upload_receipt_to_drive(
    local_file_path: str,
    status: ReimbursementStatus,
    filename: str | None = None,
) -> UploadReceiptOutput:
    return await _run_tool(
        "upload_receipt_to_drive",
        UploadReceiptOutput,
        local_file_path=local_file_path,
        status=status,
        filename=filename,
    )


@app.tool(
    name="upload_charitable_receipt_to_drive",
    description=_contract_description("upload_charitable_receipt_to_drive"),
)
async def upload_charitable_receipt_to_drive(
    local_file_path: str,
    tax_year: str | None = None,
    filename: str | None = None,
) -> UploadCharitableReceiptOutput:
    return await _run_tool(
        "upload_charitable_receipt_to_drive",
        UploadCharitableReceiptOutput,
        local_file_path=local_file_path,
        tax_year=tax_year,
        filename=filename,
    )


@app.tool(
    name="append_charitable_donation_to_ledger",
    description=_contract_description("append_charitable_donation_to_ledger"),
)
async def append_charitable_donation_to_ledger(
    donation_json: dict[str, Any],
    drive_file_id: str,
    check_duplicates: bool = True,
    force_append: bool = False,
) -> AppendCharitableDonationOutput:
    return await _run_tool(
        "append_charitable_donation_to_ledger",
        AppendCharitableDonationOutput,
        donation_json=donation_json,
        drive_file_id=drive_file_id,
        check_duplicates=check_duplicates,
        force_append=force_append,
    )


@app.tool(
    name="append_cash_charitable_donation_to_ledger",
    description=_contract_description("append_cash_charitable_donation_to_ledger"),
)
async def append_cash_charitable_donation_to_ledger(
    donation_json: dict[str, Any],
    check_duplicates: bool = True,
    force_append: bool = False,
) -> AppendCashCharitableDonationOutput:
    return await _run_tool(
        "append_cash_charitable_donation_to_ledger",
        AppendCashCharitableDonationOutput,
        donation_json=donation_json,
        check_duplicates=check_duplicates,
        force_append=force_append,
    )


@app.tool(
    name="check_charitable_duplicates",
    description=_contract_description("check_charitable_duplicates"),
)
async def check_charitable_duplicates(
    donation_json: dict[str, Any],
    fuzzy_days: int = 3,
) -> CheckCharitableDuplicatesOutput:
    return await _run_tool(
        "check_charitable_duplicates",
        CheckCharitableDuplicatesOutput,
        donation_json=donation_json,
        fuzzy_days=fuzzy_days,
    )


@app.tool(name="get_charitable_summary", description=_contract_description("get_charitable_summary"))
async def get_charitable_summary(
    tax_year: str | int | None = None,
    column_filters: list[ColumnFilter] | None = None,
) -> CharitableSummaryOutput:
    return await _run_tool(
        "get_charitable_summary",
        CharitableSummaryOutput,
        tax_year=tax_year,
        column_filters=column_filters,
    )


@app.tool(
    name="read_charitable_ledger_entries",
    description=_contract_description("read_charitable_ledger_entries"),
)
async def read_charitable_ledger_entries(
    tax_year: str | int | None = None,
    organization: str | None = None,
    tax_deductible: bool | None = None,
    limit: int = 1000,
    column_filters: list[ColumnFilter] | None = None,
) -> ReadCharitableLedgerEntriesOutput:
    return await _run_tool(
        "read_charitable_ledger_entries",
        ReadCharitableLedgerEntriesOutput,
        tax_year=tax_year,
        organization=organization,
        tax_deductible=tax_deductible,
        limit=limit,
        column_filters=column_filters,
    )


def main() -> None:
    """Main entry point."""
    app.run(transport="stdio")


if __name__ == "__main__":
    main()
