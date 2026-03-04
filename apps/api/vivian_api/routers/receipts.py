"""Receipt upload and parsing router."""

import logging
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, HTTPException, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from vivian_api.auth.dependencies import (
    CurrentUserContext,
    get_current_user_context,
)
from vivian_api.config import Settings
from vivian_api.db.database import get_db
from vivian_api.models.schemas import (
    ReceiptUploadResponse,
    ReceiptParseRequest,
    ReceiptParseResponse,
    CheckDuplicateRequest,
    CheckCharitableDuplicateRequest,
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
from vivian_api.services.mcp_registry import get_mcp_server_definitions
from vivian_api.utils import validate_temp_file_path, InvalidFilePathError
from vivian_api.chat.document_workflows import _infer_category
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


async def _create_mcp_client(
    mcp_server_id: str = "hsa_ledger",
    db: Session | None = None,
    home_id: str | None = None,
) -> MCPClient:
    """Create an MCPClient with database-backed or legacy configuration.
    
    Args:
        mcp_server_id: The MCP server to connect to
        db: Database session (optional, for DB-backed config)
        home_id: Home ID (optional, for DB-backed config)
        
    Returns:
        Configured MCPClient instance
    """
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(mcp_server_id)
    
    if not definition:
        raise ValueError(f"Unknown MCP server: {mcp_server_id}")
    
    # If we have DB session and home_id, use database-backed config
    if db is not None and home_id is not None:
        from vivian_api.services.google_integration import build_mcp_env_from_db
        env = await build_mcp_env_from_db(home_id, mcp_server_id, db, settings)
        return MCPClient(
            server_command=definition.command,
            process_env=env,
            server_path_override=definition.server_path,
            mcp_server_id=mcp_server_id,
        )
    
    # Fall back to legacy env-based config
    return MCPClient(
        server_command=definition.command,
        server_path_override=definition.server_path,
        mcp_server_id=mcp_server_id,
    )


def _get_default_home_id(current_user: CurrentUserContext) -> str:
    """Get the user's default home ID."""
    if not current_user.default_membership:
        raise HTTPException(status_code=400, detail="No home membership found")
    return current_user.default_membership.home_id


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
async def parse_receipt(
    request: ReceiptParseRequest = Body(...),
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
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

        hsa_expense = ExpenseSchema(
            provider=parsed_data.get("provider")
            or parsed_data.get("organization_name")
            or "Unknown Provider",
            service_date=parsed_data.get("service_date") or parsed_data.get("donation_date"),
            paid_date=parsed_data.get("paid_date") or parsed_data.get("donation_date"),
            amount=float(parsed_data.get("amount", 0)),
            hsa_eligible=parsed_data.get("hsa_eligible") if parsed_data.get("hsa_eligible") is not None else True,
            raw_model_output=raw_output,
        )

        charitable_donation = CharitableDonationSchema(
            organization_name=parsed_data.get("organization_name")
            or parsed_data.get("provider")
            or "Unknown Organization",
            donation_date=parsed_data.get("donation_date") or parsed_data.get("service_date"),
            amount=float(parsed_data.get("amount", 0)),
            tax_deductible=parsed_data.get("tax_deductible") if parsed_data.get("tax_deductible") is not None else True,
            description=parsed_data.get("description"),
            raw_model_output=raw_output,
        )

        if category == ExpenseCategory.CHARITABLE:
            confidence = _compute_charitable_confidence(parsed_data)
            donation = charitable_donation
            expense = hsa_expense
        else:
            confidence = _compute_hsa_confidence(parsed_data)
            donation = charitable_donation
            expense = hsa_expense

        is_duplicate = False
        duplicate_info: list[DuplicateInfo] | None = None
        duplicate_check_error: str | None = None
        mcp_client = None
        if expense and expense.hsa_eligible:
            try:
                home_id = _get_default_home_id(current_user)
                mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
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
        elif category == ExpenseCategory.CHARITABLE and donation:
            try:
                home_id = _get_default_home_id(current_user)
                mcp_client = await _create_mcp_client("charitable_ledger", db, home_id)
                await mcp_client.start()
                dup_result = await _check_charitable_duplicates(mcp_client, donation)
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
            suggested_category=category,
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
async def confirm_receipt(
    request: ConfirmReceiptRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
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
    
    # Determine which MCP server to use based on category
    mcp_server_id = "hsa_ledger" if category == ExpenseCategory.HSA else "charitable_ledger"
    
    # Initialize MCP client with database-backed configuration
    home_id = _get_default_home_id(current_user)
    mcp_client = await _create_mcp_client(mcp_server_id, db, home_id)
    await mcp_client.start()
    
    status_value = None
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
            if not request.force:
                donation_dict = charitable_data.model_dump()
                dup_result = await mcp_client.check_charitable_duplicates(donation_dict)

                if dup_result.get("is_duplicate"):
                    # Normalize charitable duplicates before constructing DuplicateInfo
                    raw_dups = dup_result.get("potential_duplicates", [])
                    normalized_dups = [_normalize_charitable_duplicate(d) for d in raw_dups]
                    return ConfirmReceiptResponse(
                        success=False,
                        message=f"Duplicate detected: {dup_result.get('recommendation', 'review')}",
                        is_duplicate=True,
                        duplicate_info=[DuplicateInfo(**d) for d in normalized_dups]
                    )

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
                status_value or ReimbursementStatus.UNREIMBURSED.value,
                drive_file_id,
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
        logger.warning("HSA duplicate check failed: %s", e, exc_info=True)
        # If duplicate check fails, return empty result (allow import)
        return {
            "is_duplicate": False,
            "potential_duplicates": [],
            "recommendation": "import",
            "check_error": str(e),
        }


def _normalize_charitable_duplicate(raw: dict) -> dict:
    """Map charitable MCP duplicate fields to DuplicateInfo-compatible dict.

    The charitable MCP tool returns ``organization`` and ``date`` whereas
    ``DuplicateInfo`` uses ``provider`` (generic name/org) and ``date``.
    """
    return {
        "entry_id": raw.get("entry_id", ""),
        "provider": raw.get("organization", raw.get("provider", "")),
        "date": raw.get("date", raw.get("donation_date", raw.get("service_date"))),
        "amount": float(raw.get("amount", 0)),
        "status": raw.get("status", ""),
        "match_type": raw.get("match_type", "exact"),
        "days_difference": raw.get("days_difference"),
        "message": raw.get("message"),
    }


async def _check_charitable_duplicates(
    mcp_client: MCPClient,
    donation: CharitableDonationSchema,
    fuzzy_days: int = 3,
) -> dict:
    """Check for duplicate charitable donations in the ledger."""
    try:
        donation_dict = donation.model_dump()
        result = await mcp_client.check_charitable_duplicates(donation_dict, fuzzy_days=fuzzy_days)
        # Normalize potential_duplicates so they match DuplicateInfo schema
        raw_dups = result.get("potential_duplicates", [])
        result["potential_duplicates"] = [
            _normalize_charitable_duplicate(d) for d in raw_dups
        ]
        return result
    except Exception as e:
        logger.warning("Charitable duplicate check failed: %s", e, exc_info=True)
        # If duplicate check fails, return empty result (allow import)
        return {
            "is_duplicate": False,
            "potential_duplicates": [],
            "recommendation": "import",
            "check_error": str(e),
        }


@router.post("/check-duplicate", response_model=CheckDuplicateResponse)
async def check_duplicate(
    request: CheckDuplicateRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Check edited expense payload for potential duplicates."""
    if not request.expense_data.hsa_eligible:
        return CheckDuplicateResponse(
            is_duplicate=False,
            duplicate_info=[],
            recommendation="import",
        )

    home_id = _get_default_home_id(current_user)
    mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
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


@router.post("/check-charitable-duplicate", response_model=CheckDuplicateResponse)
async def check_charitable_duplicate(
    request: CheckCharitableDuplicateRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Check edited charitable donation payload for potential duplicates."""
    home_id = _get_default_home_id(current_user)
    mcp_client = await _create_mcp_client("charitable_ledger", db, home_id)
    try:
        await mcp_client.start()
        dup_result = await _check_charitable_duplicates(
            mcp_client,
            request.charitable_data,
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
async def bulk_import_scan(
    request: BulkImportRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
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
    
    home_id = _get_default_home_id(current_user)
    return await _scan_file_paths(
        [str(p) for p in receipt_files],
        skip_errors=request.skip_errors,
        check_duplicates=request.check_duplicates,
        db=db,
        home_id=home_id,
    )


@router.post("/bulk-import/scan-temp", response_model=BulkImportResponse)
async def bulk_import_scan_temp(
    request: BulkImportTempScanRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Scan uploaded temp files for parsing and duplicate detection."""
    if not request.temp_file_paths:
        return BulkImportResponse(
            total_files=0,
            mode="scan",
            summary=BulkImportSummary(),
        )

    home_id = _get_default_home_id(current_user)
    return await _scan_file_paths(
        request.temp_file_paths,
        skip_errors=request.skip_errors,
        check_duplicates=request.check_duplicates,
        db=db,
        home_id=home_id,
    )


async def _scan_file_paths(
    file_paths: list[str],
    *,
    skip_errors: bool,
    check_duplicates: bool,
    db: Session,
    home_id: str,
) -> BulkImportResponse:
    parser = OpenRouterService()
    # Lazily created MCP clients - only started when needed for duplicate checks
    hsa_mcp_client: MCPClient | None = None
    charitable_mcp_client: MCPClient | None = None

    async def get_hsa_client() -> MCPClient:
        nonlocal hsa_mcp_client
        if hsa_mcp_client is None:
            hsa_mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
            await hsa_mcp_client.start()
        return hsa_mcp_client

    async def get_charitable_client() -> MCPClient:
        nonlocal charitable_mcp_client
        if charitable_mcp_client is None:
            charitable_mcp_client = await _create_mcp_client("charitable_ledger", db, home_id)
            await charitable_mcp_client.start()
        return charitable_mcp_client

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
                    ))
                    summary.failed_count += 1
                    continue

                parsed = parse_result["parsed_data"]

                # Infer category from parsed data (HSA or charitable)
                inferred_category = _infer_category(parsed)

                # Build schemas and compute confidence based on category
                warnings: list[str] = []
                expense: ExpenseSchema | None = None
                charitable_data: CharitableDonationSchema | None = None

                if inferred_category == ExpenseCategory.CHARITABLE:
                    confidence = 0.9
                    if not parsed.get("organization_name"):
                        confidence -= 0.3
                        warnings.append("Missing organization name")
                    if not parsed.get("donation_date"):
                        confidence -= 0.2
                        warnings.append("Missing donation date")
                    if not parsed.get("amount") or parsed.get("amount") == 0:
                        confidence -= 0.3
                        warnings.append("Missing or zero amount")
                    confidence = max(0, confidence)

                    charitable_data = CharitableDonationSchema(
                        organization_name=parsed.get("organization_name")
                            or parsed.get("provider")
                            or "Unknown Organization",
                        donation_date=parsed.get("donation_date") or parsed.get("service_date"),
                        amount=float(parsed.get("amount", 0)),
                        tax_deductible=parsed.get("tax_deductible") if parsed.get("tax_deductible") is not None else True,
                        description=parsed.get("description"),
                        raw_model_output=parse_result.get("raw_output"),
                    )
                else:
                    confidence = 0.9
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

                    hsa_eligible_value = parsed.get("hsa_eligible")
                    if not isinstance(hsa_eligible_value, bool):
                        hsa_eligible_value = True

                    expense = ExpenseSchema(
                        provider=parsed.get("provider", "Unknown Provider"),
                        service_date=parsed.get("service_date"),
                        paid_date=parsed.get("paid_date"),
                        amount=float(parsed.get("amount", 0)),
                        hsa_eligible=hsa_eligible_value,
                        raw_model_output=parse_result.get("raw_output"),
                    )

                needs_review = confidence < settings.confidence_threshold
                amount = float(parsed.get("amount", 0))

                # Run category-appropriate duplicate check
                duplicate_info = None
                is_duplicate = False
                if check_duplicates:
                    if inferred_category == ExpenseCategory.HSA and expense and expense.hsa_eligible:
                        dup_result = await _check_duplicates(await get_hsa_client(), expense)
                        if dup_result.get("check_error"):
                            warnings.append("Duplicate check unavailable")
                        if dup_result.get("is_duplicate"):
                            is_duplicate = True
                            duplicate_info = dup_result.get("potential_duplicates", [])
                    elif inferred_category == ExpenseCategory.CHARITABLE and charitable_data:
                        dup_result = await _check_charitable_duplicates(await get_charitable_client(), charitable_data)
                        if dup_result.get("check_error"):
                            warnings.append("Duplicate check unavailable")
                        if dup_result.get("is_duplicate"):
                            is_duplicate = True
                            duplicate_info = dup_result.get("potential_duplicates", [])

                # For HSA, skip non-eligible items
                if inferred_category == ExpenseCategory.HSA and expense and not expense.hsa_eligible:
                    failed_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="skipped",
                        expense=expense,
                        confidence=confidence,
                        category=inferred_category,
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
                        charitable_data=charitable_data,
                        confidence=confidence,
                        category=inferred_category,
                        duplicate_info=[DuplicateInfo(**d) for d in (duplicate_info or [])],
                    ))
                    summary.duplicate_count += 1
                    summary.total_amount += amount
                elif needs_review:
                    flagged_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="flagged",
                        expense=expense,
                        charitable_data=charitable_data,
                        confidence=confidence,
                        category=inferred_category,
                        warnings=warnings,
                    ))
                    summary.flagged_count += 1
                    summary.total_amount += amount
                    summary.ready_to_import += 1
                else:
                    new_results.append(BulkImportFileResult(
                        filename=receipt_path.name,
                        temp_file_path=str(receipt_path),
                        status="new",
                        expense=expense,
                        charitable_data=charitable_data,
                        confidence=confidence,
                        category=inferred_category,
                    ))
                    summary.new_count += 1
                    summary.total_amount += amount
                    summary.ready_to_import += 1
            except Exception as e:
                failed_results.append(BulkImportFileResult(
                    filename=receipt_path.name,
                    temp_file_path=str(receipt_path),
                    status="failed",
                    error=str(e),
                ))
                summary.failed_count += 1
                if not skip_errors:
                    break
    finally:
        await parser.close()
        if hsa_mcp_client is not None:
            await hsa_mcp_client.stop()
        if charitable_mcp_client is not None:
            await charitable_mcp_client.stop()

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
async def bulk_import_confirm(
    request: BulkImportConfirmRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """Confirm and import selected receipts after review.
    
    Imports selected receipts to Drive and ledger with duplicate checks.
    Uses MCP bulk tool for per-file upload + batched ledger write.
    """
    home_id = _get_default_home_id(current_user)
    # Lazily created MCP clients - only started when needed
    hsa_mcp_client: MCPClient | None = None
    charitable_mcp_client: MCPClient | None = None

    async def get_hsa_client() -> MCPClient:
        nonlocal hsa_mcp_client
        if hsa_mcp_client is None:
            hsa_mcp_client = await _create_mcp_client("hsa_ledger", db, home_id)
            await hsa_mcp_client.start()
        return hsa_mcp_client

    async def get_charitable_client() -> MCPClient:
        nonlocal charitable_mcp_client
        if charitable_mcp_client is None:
            charitable_mcp_client = await _create_mcp_client("charitable_ledger", db, home_id)
            await charitable_mcp_client.start()
        return charitable_mcp_client

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
                            hsa_eligible=parsed.get("hsa_eligible") if parsed.get("hsa_eligible") is not None else True,
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
        path_to_charitable: dict[str, CharitableDonationSchema] = {}
        local_results: list[BulkImportFileResult] = []
        mcp_payload: list[dict] = []  # HSA items for bulk MCP tool
        charitable_items: list[dict] = []  # Charitable items for individual processing

        for item in selected_items:
            temp_file_path = item["temp_file_path"] if isinstance(item, dict) else item.temp_file_path
            item_category = (
                item.get("category") if isinstance(item, dict) else item.category
            ) or ExpenseCategory.HSA
            if isinstance(item_category, str):
                item_category = ExpenseCategory(item_category)

            expense = item["expense_data"] if isinstance(item, dict) else item.expense_data
            charitable_data = item.get("charitable_data") if isinstance(item, dict) else item.charitable_data
            item_status = (
                item.get("status") if isinstance(item, dict) else item.status
            ) or request.status_override or ReimbursementStatus.UNREIMBURSED

            if isinstance(item_status, str):
                item_status = ReimbursementStatus(item_status)

            # Charitable items go through a separate path
            if item_category == ExpenseCategory.CHARITABLE:
                if not charitable_data:
                    local_results.append(BulkImportFileResult(
                        filename=Path(temp_file_path).name,
                        temp_file_path=temp_file_path,
                        status="failed",
                        category=ExpenseCategory.CHARITABLE,
                        error="Missing charitable donation data",
                    ))
                    continue
                if isinstance(charitable_data, dict):
                    charitable_data = CharitableDonationSchema(**charitable_data)
                path_to_charitable[temp_file_path] = charitable_data
                charitable_items.append({
                    "local_file_path": temp_file_path,
                    "charitable_data": charitable_data,
                    "filename": Path(temp_file_path).name,
                })
                continue

            # HSA items
            if not expense:
                local_results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    temp_file_path=temp_file_path,
                    status="failed",
                    error="Missing expense data",
                ))
                continue

            if isinstance(expense, dict):
                expense = ExpenseSchema(**expense)
            path_to_expense[temp_file_path] = expense
            if not expense.hsa_eligible:
                local_results.append(BulkImportFileResult(
                    filename=Path(temp_file_path).name,
                    temp_file_path=temp_file_path,
                    status="skipped",
                    expense=expense,
                    category=ExpenseCategory.HSA,
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
                mcp_result = await (await get_hsa_client()).bulk_import_receipts(
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
                        upload_result = await (await get_hsa_client()).upload_receipt_to_drive(
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

                        ledger_result = await (await get_hsa_client()).append_to_ledger(
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

        # Process charitable items individually (no bulk MCP tool for charitable)
        for charitable_item in charitable_items:
            c_temp_file_path = charitable_item["local_file_path"]
            c_data = charitable_item["charitable_data"]
            c_filename = charitable_item["filename"]
            try:
                upload_result = await (await get_charitable_client()).upload_charitable_receipt_to_drive(
                    c_temp_file_path,
                    filename=c_filename,
                )
                if not upload_result.get("success"):
                    local_results.append(BulkImportFileResult(
                        filename=c_filename,
                        temp_file_path=c_temp_file_path,
                        status="failed",
                        charitable_data=c_data,
                        category=ExpenseCategory.CHARITABLE,
                        error=f"Drive upload failed: {upload_result.get('error')}",
                    ))
                    continue

                ledger_result = await (await get_charitable_client()).append_charitable_donation_to_ledger(
                    c_data.model_dump(mode="json"),
                    upload_result["file_id"],
                    force_append=request.force,
                )
                if not ledger_result.get("success"):
                    dup_check = ledger_result.get("duplicate_check") or {}
                    if dup_check.get("is_duplicate"):
                        raw_dup_info = dup_check.get("potential_duplicates", [])
                        # Normalize charitable duplicates before constructing DuplicateInfo
                        normalized_dup_info = [_normalize_charitable_duplicate(d) for d in raw_dup_info]
                        has_exact = any(d.get("match_type") == "exact" for d in normalized_dup_info)
                        local_results.append(BulkImportFileResult(
                            filename=c_filename,
                            temp_file_path=c_temp_file_path,
                            status="duplicate_exact" if has_exact else "duplicate_fuzzy",
                            charitable_data=c_data,
                            category=ExpenseCategory.CHARITABLE,
                            duplicate_info=[DuplicateInfo(**d) for d in normalized_dup_info],
                            error="Duplicate entry detected",
                        ))
                        continue
                    local_results.append(BulkImportFileResult(
                        filename=c_filename,
                        temp_file_path=c_temp_file_path,
                        status="failed",
                        charitable_data=c_data,
                        category=ExpenseCategory.CHARITABLE,
                        error=f"Ledger update failed: {ledger_result.get('error')}",
                    ))
                    continue

                local_results.append(BulkImportFileResult(
                    filename=c_filename,
                    temp_file_path=c_temp_file_path,
                    status="new",
                    charitable_data=c_data,
                    category=ExpenseCategory.CHARITABLE,
                ))

                # Clean up imported temp files
                try:
                    validated_path = validate_temp_file_path(c_temp_file_path, settings.temp_upload_dir)
                    if validated_path.exists():
                        validated_path.unlink()
                except (InvalidFilePathError, FileNotFoundError):
                    pass
            except Exception as charitable_error:
                local_results.append(BulkImportFileResult(
                    filename=c_filename,
                    temp_file_path=c_temp_file_path,
                    status="failed",
                    charitable_data=c_data,
                    category=ExpenseCategory.CHARITABLE,
                    error=str(charitable_error),
                ))

        imported_count = sum(1 for r in local_results if r.status == "new")
        failed_count = sum(1 for r in local_results if r.status in {"failed", "duplicate_exact", "duplicate_fuzzy"})
        total_amount = sum(
            (r.expense.amount if r.expense else r.charitable_data.amount if r.charitable_data else 0)
            for r in local_results if r.status == "new"
        )

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
        if hsa_mcp_client is not None:
            await hsa_mcp_client.stop()
        if charitable_mcp_client is not None:
            await charitable_mcp_client.stop()
