"""Receipt upload and parsing router."""

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from vivian_api.config import Settings
from vivian_api.models.schemas import (
    ReceiptUploadResponse,
    ReceiptParseResponse,
    ConfirmReceiptRequest,
    ConfirmReceiptResponse,
    BulkImportRequest,
    BulkImportResponse,
    BulkImportFileResult,
    DuplicateInfo,
    BulkImportSummary,
    BulkImportConfirmRequest,
    BulkImportConfirmResponse,
)
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.services.mcp_client import MCPClient
from vivian_shared.models import ParsedReceipt, ExpenseSchema, ReimbursementStatus


router = APIRouter(prefix="/receipts", tags=["receipts"])
settings = Settings()


def get_temp_dir() -> Path:
    """Get or create temp upload directory."""
    temp_dir = Path(settings.temp_upload_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


@router.post("/upload", response_model=ReceiptUploadResponse)
async def upload_receipt(file: UploadFile = File(...)):
    """Upload a receipt PDF to temporary storage.
    
    Returns a temp file path that can be used for parsing.
    """
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    temp_dir = get_temp_dir()
    session_id = str(uuid.uuid4())[:8]
    temp_path = temp_dir / f"{session_id}_{file.filename}"
    
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
async def parse_receipt(temp_file_path: str):
    """Parse a previously uploaded receipt using OpenRouter.
    
    Returns structured expense data with confidence score.
    """
    parser = OpenRouterService()
    
    try:
        result = await parser.parse_receipt(temp_file_path)
        
        if not result.get("success"):
            raise HTTPException(
                status_code=422, 
                detail=f"Failed to parse receipt: {result.get('error')}"
            )
        
        parsed_data = result["parsed_data"]
        raw_output = result.get("raw_output", "")
        
        # Calculate confidence based on parsing success
        confidence = 0.9  # Start high, reduce for missing fields
        
        if not parsed_data.get("provider"):
            confidence -= 0.2
        if not parsed_data.get("service_date"):
            confidence -= 0.2
        if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
            confidence -= 0.3
        
        expense = ExpenseSchema(
            provider=parsed_data.get("provider", "Unknown Provider"),
            service_date=parsed_data.get("service_date"),
            paid_date=parsed_data.get("paid_date"),
            amount=float(parsed_data.get("amount", 0)),
            hsa_eligible=parsed_data.get("hsa_eligible", True),
            raw_model_output=raw_output
        )
        
        parsed_receipt = ParsedReceipt(
            expense=expense,
            confidence=max(0, confidence),
            parsing_errors=[] if confidence > 0.7 else ["Low confidence in some fields"]
        )
        
        needs_review = confidence < settings.confidence_threshold
        
        return ReceiptParseResponse(
            parsed_data=parsed_receipt,
            needs_review=needs_review,
            temp_file_path=temp_file_path
        )
        
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
    # Initialize MCP client
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    try:
        # Check for duplicates first (if not forcing)
        duplicate_check = None
        if not request.force:
            expense_dict = request.expense_data.model_dump()
            dup_result = await mcp_client.check_for_duplicates(expense_dict)
            duplicate_check = dup_result
            
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
            request.status.value,
            filename=None
        )
        
        if not upload_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"Drive upload failed: {upload_result.get('error')}"
            )
        
        drive_file_id = upload_result["file_id"]
        
        # Add to ledger (with duplicate check disabled since we already checked)
        expense_dict = request.expense_data.model_dump()
        expense_dict["reimbursement_date"] = (
            request.reimbursement_date.isoformat() if request.reimbursement_date else None
        )
        
        ledger_result = await mcp_client.append_to_ledger(
            expense_dict,
            request.status.value,
            drive_file_id,
            check_duplicates=False  # Already checked above
        )
        
        if not ledger_result.get("success"):
            raise HTTPException(
                status_code=500,
                detail=f"Ledger update failed: {ledger_result.get('error')}"
            )
        
        # Clean up temp file
        temp_path = Path(request.temp_file_path)
        if temp_path.exists():
            temp_path.unlink()
        
        return ConfirmReceiptResponse(
            success=True,
            ledger_entry_id=ledger_result["entry_id"],
            drive_file_id=drive_file_id,
            message="Receipt saved successfully",
            is_duplicate=False
        )
        
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
    expense: ExpenseSchema
) -> dict:
    """Check for duplicate entries in the ledger."""
    try:
        expense_dict = expense.model_dump()
        result = await mcp_client.check_for_duplicates(expense_dict)
        return result
    except Exception:
        # If duplicate check fails, return empty result (allow import)
        return {
            "is_duplicate": False,
            "potential_duplicates": [],
            "recommendation": "import"
        }


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
    
    parser = OpenRouterService()
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    new_results = []
    duplicate_results = []
    flagged_results = []
    failed_results = []
    
    summary = BulkImportSummary()
    
    try:
        for receipt_file in receipt_files:
            try:
                # Parse receipt
                parse_result = await parser.parse_receipt(str(receipt_file))
                
                if not parse_result.get("success"):
                    failed_results.append(BulkImportFileResult(
                        filename=receipt_file.name,
                        status="failed",
                        error=parse_result.get("error", "Unknown parsing error")
                    ))
                    summary.failed_count += 1
                    continue
                
                parsed = parse_result["parsed_data"]
                
                # Calculate confidence
                confidence = 0.9
                warnings = []
                
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
                    raw_model_output=parse_result.get("raw_output")
                )
                
                # Check for duplicates if enabled
                duplicate_info = None
                is_duplicate = False
                
                if request.check_duplicates and expense.hsa_eligible:
                    dup_result = await _check_duplicates(mcp_client, expense)
                    
                    if dup_result.get("is_duplicate"):
                        is_duplicate = True
                        duplicate_info = dup_result.get("potential_duplicates", [])
                
                # Determine status
                if not expense.hsa_eligible:
                    result = BulkImportFileResult(
                        filename=receipt_file.name,
                        status="skipped",
                        expense=expense,
                        confidence=confidence,
                        warnings=["Not HSA eligible"]
                    )
                    failed_results.append(result)
                    summary.failed_count += 1
                    
                elif is_duplicate:
                    # Check if exact or fuzzy
                    has_exact = any(d.get("match_type") == "exact" for d in (duplicate_info or []))
                    status = "duplicate_exact" if has_exact else "duplicate_fuzzy"
                    
                    result = BulkImportFileResult(
                        filename=receipt_file.name,
                        status=status,
                        expense=expense,
                        confidence=confidence,
                        duplicate_info=[DuplicateInfo(**d) for d in (duplicate_info or [])]
                    )
                    duplicate_results.append(result)
                    summary.duplicate_count += 1
                    summary.total_amount += expense.amount
                    
                elif needs_review:
                    result = BulkImportFileResult(
                        filename=receipt_file.name,
                        status="flagged",
                        expense=expense,
                        confidence=confidence,
                        warnings=warnings
                    )
                    flagged_results.append(result)
                    summary.flagged_count += 1
                    summary.total_amount += expense.amount
                    
                else:
                    result = BulkImportFileResult(
                        filename=receipt_file.name,
                        status="new",
                        expense=expense,
                        confidence=confidence
                    )
                    new_results.append(result)
                    summary.new_count += 1
                    summary.total_amount += expense.amount
                    summary.ready_to_import += 1
                    
            except Exception as e:
                failed_results.append(BulkImportFileResult(
                    filename=receipt_file.name,
                    status="failed",
                    error=str(e)
                ))
                summary.failed_count += 1
                
                if not request.skip_errors:
                    break
    
    finally:
        await parser.close()
        await mcp_client.stop()
    
    return BulkImportResponse(
        total_files=len(receipt_files),
        mode="scan",
        new=new_results,
        duplicates=duplicate_results,
        flagged=flagged_results,
        failed=failed_results,
        summary=summary
    )


@router.post("/bulk-import/confirm", response_model=BulkImportConfirmResponse)
async def bulk_import_confirm(request: BulkImportConfirmRequest):
    """Confirm and import selected receipts after review.
    
    Takes a list of temp file paths from the scan phase and imports them
    to Google Drive and the ledger.
    """
    parser = OpenRouterService()
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    imported = 0
    failed = 0
    total_amount = 0.0
    results = []
    
    try:
        for temp_file_path in request.temp_file_paths:
            try:
                # Re-parse the receipt
                parse_result = await parser.parse_receipt(temp_file_path)
                
                if not parse_result.get("success"):
                    results.append(BulkImportFileResult(
                        filename=Path(temp_file_path).name,
                        status="failed",
                        error=parse_result.get("error", "Parse failed")
                    ))
                    failed += 1
                    continue
                
                parsed = parse_result["parsed_data"]
                expense = ExpenseSchema(
                    provider=parsed.get("provider", "Unknown"),
                    service_date=parsed.get("service_date"),
                    paid_date=parsed.get("paid_date"),
                    amount=float(parsed.get("amount", 0)),
                    hsa_eligible=parsed.get("hsa_eligible", True),
                    raw_model_output=parse_result.get("raw_output")
                )
                
                # Skip non-HSA eligible
                if not expense.hsa_eligible:
                    results.append(BulkImportFileResult(
                        filename=Path(temp_file_path).name,
                        status="skipped",
                        expense=expense,
                        warnings=["Not HSA eligible"]
                    ))
                    continue
                
                status = request.status_override or ReimbursementStatus.UNREIMBURSED
                
                # Upload to Drive
                upload_result = await mcp_client.upload_receipt_to_drive(
                    temp_file_path,
                    status.value
                )
                
                if not upload_result.get("success"):
                    results.append(BulkImportFileResult(
                        filename=Path(temp_file_path).name,
                        status="failed",
                        expense=expense,
                        error=f"Drive upload failed: {upload_result.get('error')}"
                    ))
                    failed += 1
                    continue
                
                # Add to ledger
                expense_dict = expense.model_dump()
                ledger_result = await mcp_client.append_to_ledger(
                    expense_dict,
                    status.value,
                    upload_result["file_id"]
                )
                
                if not ledger_result.get("success"):
                    results.append(BulkImportFileResult(
                        filename=Path(temp_file_path).name,
                        status="failed",
                        expense=expense,
                        error=f"Ledger update failed: {ledger_result.get('error')}"
                    ))
                    failed += 1
                    continue
                
                results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    status="new",
                    expense=expense
                ))
                imported += 1
                total_amount += expense.amount
                
                # Clean up temp file
                temp_path = Path(temp_file_path)
                if temp_path.exists():
                    temp_path.unlink()
                    
            except Exception as e:
                results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    status="failed",
                    error=str(e)
                ))
                failed += 1
    
    finally:
        await parser.close()
        await mcp_client.stop()
    
    message = f"Successfully imported {imported} receipts"
    if failed > 0:
        message += f" ({failed} failed)"
    
    return BulkImportConfirmResponse(
        success=imported > 0,
        imported_count=imported,
        failed_count=failed,
        total_amount=total_amount,
        results=results,
        message=message
    )
