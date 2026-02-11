"""Receipt upload and parsing router."""

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Body, Depends
from fastapi.responses import JSONResponse

from vivian_api.auth.dependencies import get_current_user_context
from vivian_api.config import Settings
from vivian_api.models.schemas import (
    ReceiptUploadResponse,
    ReceiptParseRequest,
    ReceiptParseResponse,
    CheckDuplicateRequest,
    CheckDuplicateResponse,
    ConfirmReceiptRequest,
    ConfirmReceiptResponse,
    BulkImportRequest,
    BulkImportTempScanRequest,
    BulkImportResponse,
    BulkImportFileResult,
    DuplicateInfo,
    BulkImportSummary,
    BulkImportConfirmRequest,
    BulkImportConfirmResponse,
    BulkImportConfirmItem,
)
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.services.mcp_client import MCPClient
from vivian_api.utils import validate_temp_file_path, InvalidFilePathError
from vivian_shared.models import (
    ParsedReceipt,
    ExpenseSchema,
    ReimbursementStatus,
    ExpenseCategory,
    CharitableDonationSchema,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/receipts",
    tags=["receipts"],
    dependencies=[Depends(get_current_user_context)],
)
settings = Settings()


def get_temp_dir() -> Path:
    """Get or create temp upload directory."""
    temp_dir = Path(settings.temp_upload_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def _compute_hsa_confidence(parsed_data: dict) -> float:
    confidence = 0.9
    if not parsed_data.get("provider"):
        confidence -= 0.2
    if not parsed_data.get("service_date"):
        confidence -= 0.2
    if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
        confidence -= 0.3
    return max(0, confidence)


def _compute_charitable_confidence(parsed_data: dict) -> float:
    confidence = 0.9
    if not parsed_data.get("organization_name"):
        confidence -= 0.3
    if not parsed_data.get("donation_date"):
        confidence -= 0.2
    if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
        confidence -= 0.3
    return max(0, confidence)


def _infer_category(parsed_data: dict) -> ExpenseCategory:
    category_value = str(parsed_data.get("category", "")).lower().strip()
    if category_value == ExpenseCategory.CHARITABLE.value:
        return ExpenseCategory.CHARITABLE

    charitable_keys = ("organization_name", "donation_date", "tax_deductible")
    if any(parsed_data.get(key) for key in charitable_keys):
        return ExpenseCategory.CHARITABLE

    return ExpenseCategory.HSA


@router.post("/upload", response_model=ReceiptUploadResponse)
async def upload_receipt(file: UploadFile = File(...)):
    """Upload a receipt PDF to temporary storage.
    
    Returns a temp file path that can be used for parsing.
    """
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    temp_dir = get_temp_dir()
    session_id = str(uuid.uuid4())[:8]
    temp_path = temp_dir / f"{session_id}_{filename}"
    
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        return ReceiptUploadResponse(
            temp_file_path=str(temp_path),
            message="Receipt uploaded successfully. Use this path to parse."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")


@router.post("/parse", response_model=ReceiptParseResponse)
async def parse_receipt(request: ReceiptParseRequest = Body(...)):
    """Parse a previously uploaded receipt using OpenRouter.
    
    Returns structured expense data with confidence score.
    """
    parser = OpenRouterService()
    
    try:
        result = await parser.parse_receipt(request.temp_file_path)
        
        if not result.get("success"):
            raise HTTPException(
                status_code=422, 
                detail=f"Failed to parse receipt: {result.get('error')}"
            )
        
        parsed_data = result["parsed_data"]
        raw_output = result.get("raw_output", "")
        
        category = _infer_category(parsed_data)

        if category == ExpenseCategory.CHARITABLE:
            confidence = _compute_charitable_confidence(parsed_data)
            donation = CharitableDonationSchema(
                organization_name=parsed_data.get("organization_name", "Unknown Organization"),
                donation_date=parsed_data.get("donation_date"),
                amount=float(parsed_data.get("amount", 0)),
                tax_deductible=parsed_data.get("tax_deductible", True),
                description=parsed_data.get("description"),
                raw_model_output=raw_output,
            )
            expense = None
        else:
            confidence = _compute_hsa_confidence(parsed_data)
            donation = None
            expense = ExpenseSchema(
                provider=parsed_data.get("provider", "Unknown Provider"),
                service_date=parsed_data.get("service_date"),
                paid_date=parsed_data.get("paid_date"),
                amount=float(parsed_data.get("amount", 0)),
                hsa_eligible=parsed_data.get("hsa_eligible", True),
                raw_model_output=raw_output
            )

        is_duplicate = False
        duplicate_info: list[DuplicateInfo] | None = None
        duplicate_check_error: str | None = None
        mcp_client = None
        if expense and expense.hsa_eligible:
            try:
                mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
                await mcp_client.start()
                dup_result = await _check_duplicates(mcp_client, expense)
                if dup_result.get("is_duplicate"):
                    is_duplicate = True
                    duplicate_info = [DuplicateInfo(**d) for d in dup_result.get("potential_duplicates", [])]
                if dup_result.get("check_error"):
                    duplicate_check_error = str(dup_result["check_error"])
            except Exception as dup_error:
                duplicate_check_error = f"Duplicate check unavailable: {dup_error}"
            finally:
                if mcp_client:
                    await mcp_client.stop()
        
        parsed_receipt = ParsedReceipt(
            category=category,
            expense=expense,
            charitable_data=donation,
            confidence=max(0, confidence),
            parsing_errors=[] if confidence > 0.7 else ["Low confidence in some fields"]
        )
        
        needs_review = confidence < settings.confidence_threshold
        
        return ReceiptParseResponse(
            parsed_data=parsed_receipt,
            needs_review=needs_review,
            temp_file_path=request.temp_file_path,
            is_duplicate=is_duplicate,
            duplicate_info=duplicate_info,
            duplicate_check_error=duplicate_check_error,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing failed: {str(e)}")
    finally:
        await parser.close()


@router.post("/confirm", response_model=ConfirmReceiptResponse)
async def confirm_receipt(request: ConfirmReceiptRequest):
    """Confirm and save a parsed receipt to Drive and Ledger.
    
    This is the final step after user confirmation/editing.
    Checks for duplicates before saving.
    """
    # Validate file path to prevent path traversal attacks
    try:
        validated_path = validate_temp_file_path(
            request.temp_file_path,
            settings.temp_upload_dir
        )
    except (InvalidFilePathError, FileNotFoundError) as exc:
        logger.warning(
            "File validation failed in confirm receipt endpoint",
            extra={"error_type": type(exc).__name__}
        )
        raise HTTPException(
            status_code=400,
            detail="Invalid or inaccessible file. Please ensure the file was uploaded correctly."
        )
    
    category = request.category or ExpenseCategory.HSA
    status = request.status if category == ExpenseCategory.HSA else None
    if category == ExpenseCategory.CHARITABLE:
        charitable_data = request.charitable_data
        if not charitable_data:
            raise HTTPException(status_code=422, detail="charitable_data is required for charitable receipts")
    else:
        expense_data = request.expense_data
        if not expense_data:
            raise HTTPException(status_code=422, detail="expense_data is required for HSA receipts")
        if not status:
            raise HTTPException(status_code=422, detail="status is required for HSA receipts")

    # Initialize MCP client
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    try:
        # Check for duplicates first (if not forcing)
        if category == ExpenseCategory.HSA:
            expense_data = request.expense_data
            if expense_data is None:
                raise HTTPException(status_code=422, detail="expense_data is required for HSA receipts")
            if status is None:
                raise HTTPException(status_code=422, detail="status is required for HSA receipts")
            status_value = status.value
            if not request.force:
                expense_dict = expense_data.model_dump()
                dup_result = await mcp_client.check_for_duplicates(expense_dict)

                if dup_result.get("is_duplicate"):
                    return ConfirmReceiptResponse(
                        success=False,
                        message=f"Duplicate detected: {dup_result.get('recommendation', 'review')}",
                        is_duplicate=True,
                        duplicate_info=[DuplicateInfo(**d) for d in dup_result.get("potential_duplicates", [])]
                    )

            # Upload to Google Drive
            upload_result = await mcp_client.upload_receipt_to_drive(
                request.temp_file_path,
                status_value,
                filename=None
            )
        else:
            charitable_data = request.charitable_data
            if charitable_data is None:
                raise HTTPException(status_code=422, detail="charitable_data is required for charitable receipts")
            donation_year = (
                charitable_data.donation_date.year
                if charitable_data.donation_date
                else None
            )
            upload_result = await mcp_client.upload_charitable_receipt_to_drive(
                request.temp_file_path,
                donation_year=donation_year,
                filename=None,
            )
        
        if not upload_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"Drive upload failed: {upload_result.get('error')}"
            )
        
        drive_file_id = upload_result["file_id"]
        
        # Add to ledger
        if category == ExpenseCategory.HSA:
            expense_data = request.expense_data
            if expense_data is None:
                raise HTTPException(status_code=422, detail="expense_data is required for HSA receipts")
            expense_dict = expense_data.model_dump()
            expense_dict["reimbursement_date"] = (
                request.reimbursement_date.isoformat() if request.reimbursement_date else None
            )

            ledger_result = await mcp_client.append_to_ledger(
                expense_dict,
                status_value,
                drive_file_id
            )
        else:
            charitable_data = request.charitable_data
            if charitable_data is None:
                raise HTTPException(status_code=422, detail="charitable_data is required for charitable receipts")
            donation_json = charitable_data.model_dump(mode="json")
            ledger_result = await mcp_client.append_charitable_donation_to_ledger(
                donation_json,
                drive_file_id,
                force_append=request.force,
            )
        
        if not ledger_result.get("success"):
            duplicate_check = ledger_result.get("duplicate_check") or {}
            duplicate_error = str(ledger_result.get("error") or "").lower()

            if category == ExpenseCategory.HSA:
                # If append step reports duplicate, surface it as a duplicate response
                # so the client can show skip/override instead of a generic failure.
                if not request.force and (
                    duplicate_check.get("is_duplicate") or "duplicate" in duplicate_error
                ):
                    potential_duplicates = duplicate_check.get("potential_duplicates", [])
                    return ConfirmReceiptResponse(
                        success=False,
                        message=f"Duplicate detected: {duplicate_check.get('recommendation', 'review')}",
                        is_duplicate=True,
                        duplicate_info=[DuplicateInfo(**d) for d in potential_duplicates],
                    )

            raise HTTPException(
                status_code=500,
                detail=f"Ledger update failed: {ledger_result.get('error')}"
            )
        
        # Clean up temp file (use validated path)
        if validated_path.exists():
            validated_path.unlink()
        
        return ConfirmReceiptResponse(
            success=True,
            ledger_entry_id=ledger_result["entry_id"],
            drive_file_id=drive_file_id,
            message="Receipt saved successfully",
            is_duplicate=False
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save receipt: {str(e)}")
    finally:
        await mcp_client.stop()


def _get_receipt_files(directory: Path) -> list[Path]:
    """Get all receipt files (PDF, JPG, PNG, JPEG) from directory."""
    extensions = ["*.pdf", "*.jpg", "*.jpeg", "*.png"]
    files = []
    for ext in extensions:
        files.extend(directory.glob(ext))
    return sorted(files)


async def _check_duplicates(
    mcp_client: MCPClient,
    expense: ExpenseSchema,
    fuzzy_days: int = 3,
) -> dict:
    """Check for duplicate entries in the ledger."""
    try:
        expense_dict = expense.model_dump()
        result = await mcp_client.check_for_duplicates(expense_dict, fuzzy_days=fuzzy_days)
        return result
    except Exception as e:
        # If duplicate check fails, return empty result (allow import)
        return {
            "is_duplicate": False,
            "potential_duplicates": [],
            "recommendation": "import",
            "check_error": str(e),
        }


@router.post("/check-duplicate", response_model=CheckDuplicateResponse)
async def check_duplicate(request: CheckDuplicateRequest):
    """Check edited expense payload for potential duplicates."""
    if not request.expense_data.hsa_eligible:
        return CheckDuplicateResponse(
            is_duplicate=False,
            duplicate_info=[],
            recommendation="import",
        )

    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    try:
        await mcp_client.start()
        dup_result = await _check_duplicates(
            mcp_client,
            request.expense_data,
            fuzzy_days=request.fuzzy_days,
        )
        return CheckDuplicateResponse(
            is_duplicate=bool(dup_result.get("is_duplicate")),
            duplicate_info=[DuplicateInfo(**d) for d in dup_result.get("potential_duplicates", [])],
            recommendation=str(dup_result.get("recommendation", "import")),
            check_error=str(dup_result.get("check_error")) if dup_result.get("check_error") else None,
        )
    except Exception as e:
        return CheckDuplicateResponse(
            is_duplicate=False,
            duplicate_info=[],
            recommendation="import",
            check_error=f"Duplicate check unavailable: {e}",
        )
    finally:
        await mcp_client.stop()


@router.post("/bulk-import/scan", response_model=BulkImportResponse)
async def bulk_import_scan(request: BulkImportRequest):
    """Scan receipts for parsing and duplicate detection without saving.
    
    This is a preview mode that parses receipts and checks for duplicates
    without uploading to Drive or adding to the ledger.
    """
    directory = Path(request.directory_path)
    if not directory.exists():
        raise HTTPException(status_code=400, detail="Directory not found")
    
    receipt_files = _get_receipt_files(directory)
    
    if not receipt_files:
        return BulkImportResponse(
            total_files=0,
            mode="scan",
            summary=BulkImportSummary()
        )
    
    return await _scan_file_paths(
        [str(p) for p in receipt_files],
        skip_errors=request.skip_errors,
        check_duplicates=request.check_duplicates,
    )


@router.post("/bulk-import/scan-temp", response_model=BulkImportResponse)
async def bulk_import_scan_temp(request: BulkImportTempScanRequest):
    """Scan uploaded temp files for parsing and duplicate detection."""
    if not request.temp_file_paths:
        return BulkImportResponse(
            total_files=0,
            mode="scan",
            summary=BulkImportSummary(),
        )

    return await _scan_file_paths(
        request.temp_file_paths,
        skip_errors=request.skip_errors,
        check_duplicates=request.check_duplicates,
    )


async def _scan_file_paths(
    file_paths: list[str],
    *,
    skip_errors: bool,
    check_duplicates: bool,
) -> BulkImportResponse:
    parser = OpenRouterService()
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()

    new_results: list[BulkImportFileResult] = []
    duplicate_results: list[BulkImportFileResult] = []
    flagged_results: list[BulkImportFileResult] = []
    failed_results: list[BulkImportFileResult] = []

    summary = BulkImportSummary()

    try:
        for file_path in file_paths:
            receipt_path = Path(file_path)
            try:
                parse_result = await parser.parse_receipt(str(receipt_path))
                if not parse_result.get("success"):
                    failed_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="failed",
                        error=parse_result.get("error", "Unknown parsing error"),
                        category=ExpenseCategory.HSA,
                    ))
                    summary.failed_count += 1
                    continue

                parsed = parse_result["parsed_data"]
                confidence = 0.9
                warnings: list[str] = []

                if not parsed.get("provider"):
                    confidence -= 0.2
                    warnings.append("Missing provider")
                if not parsed.get("service_date"):
                    confidence -= 0.2
                    warnings.append("Missing service date")
                if not parsed.get("amount") or parsed.get("amount") == 0:
                    confidence -= 0.3
                    warnings.append("Missing or zero amount")

                confidence = max(0, confidence)
                needs_review = confidence < settings.confidence_threshold

                expense = ExpenseSchema(
                    provider=parsed.get("provider", "Unknown Provider"),
                    service_date=parsed.get("service_date"),
                    paid_date=parsed.get("paid_date"),
                    amount=float(parsed.get("amount", 0)),
                    hsa_eligible=parsed.get("hsa_eligible", True),
                    raw_model_output=parse_result.get("raw_output"),
                )

                duplicate_info = None
                is_duplicate = False
                if check_duplicates and expense.hsa_eligible:
                    dup_result = await _check_duplicates(mcp_client, expense)
                    if dup_result.get("check_error"):
                        warnings.append("Duplicate check unavailable")
                    if dup_result.get("is_duplicate"):
                        is_duplicate = True
                        duplicate_info = dup_result.get("potential_duplicates", [])

                if not expense.hsa_eligible:
                    failed_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="skipped",
                        expense=expense,
                        confidence=confidence,
                        category=ExpenseCategory.HSA,
                        warnings=["Not HSA eligible"],
                    ))
                    summary.failed_count += 1
                elif is_duplicate:
                    has_exact = any(d.get("match_type") == "exact" for d in (duplicate_info or []))
                    status = "duplicate_exact" if has_exact else "duplicate_fuzzy"
                    duplicate_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status=status,
                        expense=expense,
                        confidence=confidence,
                        category=ExpenseCategory.HSA,
                        duplicate_info=[DuplicateInfo(**d) for d in (duplicate_info or [])],
                    ))
                    summary.duplicate_count += 1
                    summary.total_amount += expense.amount
                elif needs_review:
                    flagged_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="flagged",
                        expense=expense,
                        confidence=confidence,
                        category=ExpenseCategory.HSA,
                        warnings=warnings,
                    ))
                    summary.flagged_count += 1
                    summary.total_amount += expense.amount
                    summary.ready_to_import += 1
                else:
                    new_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="new",
                        expense=expense,
                        confidence=confidence,
                        category=ExpenseCategory.HSA,
                    ))
                    summary.new_count += 1
                    summary.total_amount += expense.amount
                    summary.ready_to_import += 1
            except Exception as e:
                failed_results.append(BulkImportFileResult(
                    filename=receipt_path.name,
                    temp_file_path=str(receipt_path),
                    status="failed",
                    error=str(e),
                    category=ExpenseCategory.HSA,
                ))
                summary.failed_count += 1
                if not skip_errors:
                    break
    finally:
        await parser.close()
        await mcp_client.stop()

    return BulkImportResponse(
        total_files=len(file_paths),
        mode="scan",
        new=new_results,
        duplicates=duplicate_results,
        flagged=flagged_results,
        failed=failed_results,
        summary=summary,
    )


@router.post("/bulk-import/confirm", response_model=BulkImportConfirmResponse)
async def bulk_import_confirm(request: BulkImportConfirmRequest):
    """Confirm and import selected receipts after review.
    
    Imports selected receipts to Drive and ledger with duplicate checks.
    Uses MCP bulk tool for per-file upload + batched ledger write.
    """
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()

    legacy_parser = None
    try:
        selected_items = list(request.items)

        # Legacy fallback for old clients that only send temp_file_paths.
        if not selected_items and request.temp_file_paths:
            legacy_parser = OpenRouterService()
            for temp_file_path in request.temp_file_paths:
                parse_result = await legacy_parser.parse_receipt(temp_file_path)
                if not parse_result.get("success"):
                    continue
                parsed = parse_result["parsed_data"]
                selected_items.append(
                    BulkImportConfirmItem(
                        temp_file_path=temp_file_path,
                        expense_data=ExpenseSchema(
                            provider=parsed.get("provider", "Unknown"),
                            service_date=parsed.get("service_date"),
                            paid_date=parsed.get("paid_date"),
                            amount=float(parsed.get("amount", 0)),
                            hsa_eligible=parsed.get("hsa_eligible", True),
                            raw_model_output=parse_result.get("raw_output"),
                        ),
                    )
                )

        if not selected_items:
            return BulkImportConfirmResponse(
                success=False,
                imported_count=0,
                failed_count=0,
                total_amount=0.0,
                results=[],
                message="No receipts selected for import",
            )

        path_to_expense: dict[str, ExpenseSchema] = {}
        local_results: list[BulkImportFileResult] = []
        mcp_payload: list[dict] = []

        for item in selected_items:
            temp_file_path = item["temp_file_path"] if isinstance(item, dict) else item.temp_file_path
            expense = item["expense_data"] if isinstance(item, dict) else item.expense_data
            item_status = (
                item.get("status") if isinstance(item, dict) else item.status
            ) or request.status_override or ReimbursementStatus.UNREIMBURSED

            if isinstance(item_status, str):
                item_status = ReimbursementStatus(item_status)

            if not expense:
                local_results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    temp_file_path=temp_file_path,
                    status="failed",
                    error="Missing expense data",
                ))
                continue

            path_to_expense[temp_file_path] = expense
            if not expense.hsa_eligible:
                local_results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    temp_file_path=temp_file_path,
                    status="skipped",
                    expense=expense,
                    warnings=["Not HSA eligible"],
                ))
                continue

            mcp_payload.append(
                {
                    "local_file_path": temp_file_path,
                    "expense_json": expense.model_dump(mode="json"),
                    "reimbursement_status": item_status.value,
                    "filename": Path(temp_file_path).name,
                }
            )

        if mcp_payload:
            try:
                mcp_result = await mcp_client.bulk_import_receipts(
                    mcp_payload,
                    check_duplicates=True,
                    force_append=request.force,
                )
            except Exception as bulk_error:
                # Fallback: process each receipt through existing MCP calls.
                fallback_results: list[dict] = []
                for payload in mcp_payload:
                    temp_file_path = payload.get("local_file_path", "")
                    expense_json = payload.get("expense_json", {})
                    filename = payload.get("filename") or Path(temp_file_path).name
                    try:
                        upload_result = await mcp_client.upload_receipt_to_drive(
                            temp_file_path,
                            payload.get("reimbursement_status", ReimbursementStatus.UNREIMBURSED.value),
                            filename=filename,
                        )
                        if not upload_result.get("success"):
                            fallback_results.append(
                                {
                                    "filename": filename,
                                    "local_file_path": temp_file_path,
                                    "temp_file_path": temp_file_path,
                                    "status": "failed",
                                    "error": f"Drive upload failed: {upload_result.get('error')}",
                                }
                            )
                            continue

                        ledger_result = await mcp_client.append_to_ledger(
                            expense_json,
                            payload.get("reimbursement_status", ReimbursementStatus.UNREIMBURSED.value),
                            upload_result["file_id"],
                            force_append=request.force,
                        )
                        if not ledger_result.get("success"):
                            duplicate_check = ledger_result.get("duplicate_check") or {}
                            if duplicate_check.get("is_duplicate"):
                                duplicate_info = duplicate_check.get("potential_duplicates", [])
                                has_exact = any(d.get("match_type") == "exact" for d in duplicate_info)
                                fallback_results.append(
                                    {
                                        "filename": filename,
                                        "local_file_path": temp_file_path,
                                        "temp_file_path": temp_file_path,
                                        "status": "duplicate_exact" if has_exact else "duplicate_fuzzy",
                                        "duplicate_info": duplicate_info,
                                        "error": "Duplicate entry detected",
                                    }
                                )
                                continue
                            fallback_results.append(
                                {
                                    "filename": filename,
                                    "local_file_path": temp_file_path,
                                    "temp_file_path": temp_file_path,
                                    "status": "failed",
                                    "error": f"Ledger update failed: {ledger_result.get('error')}",
                                }
                            )
                            continue

                        fallback_results.append(
                            {
                                "filename": filename,
                                "local_file_path": temp_file_path,
                                "temp_file_path": temp_file_path,
                                "status": "imported",
                                "entry_id": ledger_result.get("entry_id"),
                                "drive_file_id": upload_result.get("file_id"),
                            }
                        )
                    except Exception as item_error:
                        fallback_results.append(
                            {
                                "filename": filename,
                                "local_file_path": temp_file_path,
                                "temp_file_path": temp_file_path,
                                "status": "failed",
                                "error": str(item_error),
                            }
                        )

                mcp_result = {
                    "results": fallback_results,
                    "error": f"MCP bulk tool failed, used fallback path: {str(bulk_error)}",
                }
        else:
            mcp_result = {"results": []}

        mcp_results = mcp_result.get("results", [])
        if not mcp_results and mcp_result.get("error"):
            for payload in mcp_payload:
                temp_file_path = payload.get("local_file_path", "")
                expense = path_to_expense.get(temp_file_path)
                local_results.append(
                    BulkImportFileResult(
                        filename=payload.get("filename") or Path(temp_file_path).name,
                        temp_file_path=temp_file_path or None,
                        status="failed",
                        expense=expense,
                        error=str(mcp_result.get("error")),
                    )
                )
            mcp_results = []

        for result in mcp_results:
            temp_file_path = result.get("temp_file_path") or result.get("local_file_path") or ""
            expense = path_to_expense.get(temp_file_path)

            raw_status = result.get("status", "failed")
            if raw_status == "imported":
                status_label = "new"
            elif raw_status in {"duplicate_exact", "duplicate_fuzzy"}:
                status_label = raw_status
            elif raw_status == "skipped":
                status_label = "skipped"
            else:
                status_label = "failed"

            duplicate_info = None
            if result.get("duplicate_info"):
                duplicate_info = [DuplicateInfo(**d) for d in result["duplicate_info"]]

            local_results.append(
                BulkImportFileResult(
                    filename=result.get("filename", Path(temp_file_path).name if temp_file_path else "unknown"),
                    temp_file_path=temp_file_path or None,
                    status=status_label,
                    expense=expense,
                    duplicate_info=duplicate_info,
                    error=result.get("error"),
                    warnings=result.get("warnings") or [],
                )
            )

            # Clean up imported temp files
            if status_label == "new" and temp_file_path:
                try:
                    validated_path = validate_temp_file_path(
                        temp_file_path,
                        settings.temp_upload_dir
                    )
                    if validated_path.exists():
                        validated_path.unlink()
                except (InvalidFilePathError, FileNotFoundError):
                    # If path validation fails, skip cleanup but don't fail the import
                    pass

        imported_count = sum(1 for r in local_results if r.status == "new")
        failed_count = sum(1 for r in local_results if r.status in {"failed", "duplicate_exact", "duplicate_fuzzy"})
        total_amount = sum(r.expense.amount for r in local_results if r.status == "new" and r.expense is not None)

        message = f"Successfully imported {imported_count} receipts"
        if failed_count > 0:
            message += f" ({failed_count} failed)"

        return BulkImportConfirmResponse(
            success=imported_count > 0,
            imported_count=imported_count,
            failed_count=failed_count,
            total_amount=total_amount,
            results=local_results,
            message=message,
        )
    finally:
        if legacy_parser is not None:
            await legacy_parser.close()
        await mcp_client.stop()
