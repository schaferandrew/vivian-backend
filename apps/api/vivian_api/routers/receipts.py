"""Receipt upload and parsing router."""

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from vivian_api.config import Settings
from vivian_api.models.schemas import (
    ReceiptUploadResponse, 
    ParseReceiptRequest,
    ReceiptParseResponse,
    ConfirmReceiptRequest,
    ConfirmReceiptResponse,
    BulkImportRequest,
    BulkImportResponse,
    BulkImportFileResult
)
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.services.mcp_client import MCPClient, MCPClientError
from vivian_shared.models import ParsedReceipt, ExpenseSchema, ReimbursementStatus


router = APIRouter(prefix="/receipts", tags=["receipts"])
settings = Settings()
logger = logging.getLogger(__name__)


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
        logger.exception("Failed to save uploaded receipt to temporary storage: %s", temp_path)
        raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")


@router.post("/parse", response_model=ReceiptParseResponse)
async def parse_receipt(request: ParseReceiptRequest):
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
            temp_file_path=request.temp_file_path
        )
        
    except HTTPException as exc:
        if exc.status_code >= 500:
            logger.error(
                "Receipt parse failed with HTTPException for temp_file_path=%s: %s",
                request.temp_file_path,
                exc.detail,
            )
        raise
    except Exception as e:
        logger.exception("Unexpected error while parsing receipt temp_file_path=%s", request.temp_file_path)
        raise HTTPException(status_code=500, detail=f"Parsing failed: {str(e)}")
    finally:
        await parser.close()


@router.post("/confirm", response_model=ConfirmReceiptResponse)
async def confirm_receipt(request: ConfirmReceiptRequest):
    """Confirm and save a parsed receipt to Drive and Ledger.
    
    This is the final step after user confirmation/editing.
    """
    # Initialize MCP client
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    try:
        # Non-HSA-eligible receipts should not be persisted.
        if (
            request.status == ReimbursementStatus.NOT_HSA_ELIGIBLE
            or not request.expense_data.hsa_eligible
        ):
            temp_path = Path(request.temp_file_path)
            if temp_path.exists():
                temp_path.unlink()

            return ConfirmReceiptResponse(
                success=True,
                message=(
                    "Receipt marked not HSA-eligible. No Google Drive upload or "
                    "ledger entry was created."
                )
            )

        # Upload to Google Drive
        upload_result = await mcp_client.upload_receipt_to_drive(
            request.temp_file_path,
            request.status.value,
            filename=None
        )
        
        if not upload_result.get("success"):
            logger.error(
                "Drive upload failed for temp_file_path=%s status=%s error=%s",
                request.temp_file_path,
                request.status.value,
                upload_result.get("error"),
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not upload receipt to Google Drive. "
                    f"{upload_result.get('error', 'Unknown error')}"
                )
            )

        drive_file_id = upload_result.get("file_id")
        if not drive_file_id:
            logger.error(
                "Drive upload response missing file_id for temp_file_path=%s status=%s payload=%s",
                request.temp_file_path,
                request.status.value,
                upload_result,
            )
            raise HTTPException(
                status_code=502,
                detail="Drive upload did not return a file ID. Please try again."
            )
        
        # Add to ledger
        expense_dict = request.expense_data.model_dump()
        expense_dict["reimbursement_date"] = (
            request.reimbursement_date.isoformat() if request.reimbursement_date else None
        )
        
        ledger_result = await mcp_client.append_to_ledger(
            expense_dict,
            request.status.value,
            drive_file_id
        )
        
        if not ledger_result.get("success"):
            logger.error(
                "Ledger update failed for drive_file_id=%s status=%s error=%s",
                drive_file_id,
                request.status.value,
                ledger_result.get("error"),
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not update Google Sheet ledger. "
                    f"{ledger_result.get('error', 'Unknown error')}"
                )
            )
        
        # Clean up temp file
        temp_path = Path(request.temp_file_path)
        if temp_path.exists():
            temp_path.unlink()
        
        return ConfirmReceiptResponse(
            success=True,
            ledger_entry_id=ledger_result["entry_id"],
            drive_file_id=drive_file_id,
            message="Receipt saved successfully"
        )
        
    except HTTPException as exc:
        if exc.status_code >= 500:
            logger.error(
                "Receipt confirm failed for temp_file_path=%s status=%s: %s",
                request.temp_file_path,
                request.status.value,
                exc.detail,
            )
        raise
    except MCPClientError as e:
        logger.exception(
            "MCP client error while saving receipt temp_file_path=%s status=%s",
            request.temp_file_path,
            request.status.value,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Could not save receipt right now. {str(e)}"
        )
    except Exception:
        logger.exception(
            "Unexpected error while confirming receipt temp_file_path=%s status=%s",
            request.temp_file_path,
            request.status.value,
        )
        raise HTTPException(
            status_code=500,
            detail="Could not save receipt due to an unexpected server error."
        )
    finally:
        await mcp_client.stop()


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_receipts(request: BulkImportRequest):
    """Bulk import all PDF receipts from a directory.
    
    Parses each receipt, uploads to Drive, and adds to ledger.
    """
    import os
    from glob import glob
    
    directory = Path(request.directory_path)
    if not directory.exists():
        raise HTTPException(status_code=400, detail="Directory not found")
    
    pdf_files = list(directory.glob("*.pdf"))
    
    if not pdf_files:
        return BulkImportResponse(
            total_files=0,
            successful=0,
            failed=0,
            results=[]
        )
    
    parser = OpenRouterService()
    mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
    await mcp_client.start()
    
    results = []
    successful = 0
    failed = 0
    
    try:
        for pdf_file in pdf_files:
            try:
                # Parse receipt
                parse_result = await parser.parse_receipt(str(pdf_file))
                
                if not parse_result.get("success"):
                    results.append(BulkImportFileResult(
                        filename=pdf_file.name,
                        success=False,
                        error=parse_result.get("error", "Unknown parsing error")
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
                
                # Skip non-HSA-eligible receipts entirely.
                if not expense.hsa_eligible:
                    results.append(BulkImportFileResult(
                        filename=pdf_file.name,
                        success=False,
                        error="Skipped: marked not HSA-eligible"
                    ))
                    continue

                # Determine status
                status = request.status_override or ReimbursementStatus.UNREIMBURSED
                
                # Upload to Drive
                upload_result = await mcp_client.upload_receipt_to_drive(
                    str(pdf_file),
                    status.value
                )
                
                if not upload_result.get("success"):
                    results.append(BulkImportFileResult(
                        filename=pdf_file.name,
                        success=False,
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
                        filename=pdf_file.name,
                        success=False,
                        error=f"Ledger update failed: {ledger_result.get('error')}"
                    ))
                    failed += 1
                    continue
                
                results.append(BulkImportFileResult(
                    filename=pdf_file.name,
                    success=True,
                    expense=expense
                ))
                successful += 1
                
            except Exception as e:
                results.append(BulkImportFileResult(
                    filename=pdf_file.name,
                    success=False,
                    error=str(e)
                ))
                failed += 1
                
                if not request.skip_errors:
                    break
    
    finally:
        await parser.close()
        await mcp_client.stop()
    
    return BulkImportResponse(
        total_files=len(pdf_files),
        successful=successful,
        failed=failed,
        results=results
    )
