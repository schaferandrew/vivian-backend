"""Document workflow routing for chat attachments."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from vivian_api.config import Settings
from vivian_api.services.mcp_client import MCPClient
from vivian_api.services.mcp_registry import get_mcp_server_definitions
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.utils import validate_temp_file_path, InvalidFilePathError
from vivian_shared.models import (
    ExpenseSchema,
    ParsedReceipt,
    ExpenseCategory,
    CharitableDonationSchema,
)


logger = logging.getLogger(__name__)
DocumentType = Literal["receipt", "hsa_receipt", "charitable_receipt"]


def _infer_category(parsed_data: dict) -> ExpenseCategory:
    category_value = str(parsed_data.get("category", "")).lower().strip()
    if category_value == ExpenseCategory.CHARITABLE.value:
        return ExpenseCategory.CHARITABLE

    charitable_keys = ("organization_name", "donation_date", "tax_deductible")
    if any(parsed_data.get(key) for key in charitable_keys):
        return ExpenseCategory.CHARITABLE

    return ExpenseCategory.HSA


class ChatAttachment(BaseModel):
    """Attachment metadata sent from chat clients."""

    attachment_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:10]}")
    document_type: DocumentType = "receipt"
    temp_file_path: str
    filename: str | None = None
    mime_type: str | None = None


class DocumentWorkflowArtifact(BaseModel):
    """Client-renderable workflow state for a parsed attachment."""

    workflow_id: str = Field(default_factory=lambda: f"wf_{uuid.uuid4().hex[:10]}")
    attachment_id: str | None = None
    document_type: str
    status: Literal[
        "ready_for_confirmation",
        "unsupported",
        "parse_error",
        "configuration_error",
    ]
    message: str
    temp_file_path: str | None = None
    filename: str | None = None
    parsed_data: dict[str, Any] | None = None
    is_duplicate: bool | None = None
    duplicate_info: list[dict[str, Any]] = Field(default_factory=list)
    duplicate_check_error: str | None = None


class DocumentWorkflowExecution(BaseModel):
    """Result of routing one or more attachments through workflow handlers."""

    response_text: str
    artifacts: list[DocumentWorkflowArtifact] = Field(default_factory=list)
    tools_called: list[dict[str, str]] = Field(default_factory=list)


DocumentWorkflowHandler = Callable[
    [ChatAttachment, list[str], Settings],
    Awaitable[tuple[DocumentWorkflowArtifact, list[dict[str, str]]]],
]


def _json_compact(value: Any) -> str:
    """Safely convert values to compact JSON strings for metadata."""
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


def _compute_receipt_confidence(parsed_data: dict[str, Any]) -> float:
    """Match confidence heuristics used in receipts router."""
    confidence = 0.9
    if not parsed_data.get("provider"):
        confidence -= 0.2
    if not parsed_data.get("service_date"):
        confidence -= 0.2
    if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
        confidence -= 0.3
    return max(0, confidence)


def _compute_charitable_confidence(parsed_data: dict[str, Any]) -> float:
    """Confidence heuristic for charitable receipts."""
    confidence = 0.9
    if not parsed_data.get("organization_name"):
        confidence -= 0.3
    if not parsed_data.get("donation_date"):
        confidence -= 0.2
    if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
        confidence -= 0.3
    return max(0, confidence)


async def _run_receipt_workflow(
    attachment: ChatAttachment,
    enabled_mcp_servers: list[str],
    settings: Settings,
) -> tuple[DocumentWorkflowArtifact, list[dict[str, str]]]:
    """Parse a receipt (HSA or charitable) and prepare data for confirmation UI."""
    tools_called: list[dict[str, str]] = []
    parser = OpenRouterService()

    # Validate file path to prevent path traversal attacks
    try:
        validated_path = validate_temp_file_path(
            attachment.temp_file_path,
            settings.temp_upload_dir
        )
    except (InvalidFilePathError, FileNotFoundError) as exc:
        logger.warning(
            "File validation failed for chat attachment",
            extra={"attachment_id": attachment.attachment_id, "error_type": type(exc).__name__}
        )
        return (
            DocumentWorkflowArtifact(
                attachment_id=attachment.attachment_id,
                document_type="receipt",
                status="parse_error",
                message="Could not access the uploaded file. Please try uploading again.",
                temp_file_path=attachment.temp_file_path,
                filename=attachment.filename,
            ),
            tools_called,
        )

    try:
        parse_result = await parser.parse_receipt(str(validated_path))
    except Exception as exc:
        return (
            DocumentWorkflowArtifact(
                attachment_id=attachment.attachment_id,
                document_type="receipt",
                status="parse_error",
                message=f"Could not parse this receipt: {exc}",
                temp_file_path=attachment.temp_file_path,
                filename=attachment.filename,
            ),
            tools_called,
        )
    finally:
        try:
            await parser.close()
        except Exception:
            pass

    if not parse_result.get("success"):
        return (
            DocumentWorkflowArtifact(
                attachment_id=attachment.attachment_id,
                document_type="receipt",
                status="parse_error",
                message=str(parse_result.get("error", "Failed to parse receipt.")),
                temp_file_path=attachment.temp_file_path,
                filename=attachment.filename,
            ),
            tools_called,
        )

    parsed_data = parse_result.get("parsed_data") or {}
    raw_output = str(parse_result.get("raw_output", ""))
    
    # Infer category from parsed data
    inferred_category = _infer_category(parsed_data)
    
    # Compute confidence based on category
    if inferred_category == ExpenseCategory.CHARITABLE:
        confidence = _compute_charitable_confidence(parsed_data)
    else:
        confidence = _compute_receipt_confidence(parsed_data)

    # Build HSA expense schema
    hsa_eligible_value = parsed_data.get("hsa_eligible")
    if not isinstance(hsa_eligible_value, bool):
        hsa_eligible_value = inferred_category == ExpenseCategory.HSA

    expense = ExpenseSchema(
        provider=parsed_data.get("provider")
        or parsed_data.get("organization_name")
        or "Unknown Provider",
        service_date=parsed_data.get("service_date") or parsed_data.get("donation_date"),
        paid_date=parsed_data.get("paid_date") or parsed_data.get("donation_date"),
        amount=float(parsed_data.get("amount", 0)),
        hsa_eligible=hsa_eligible_value,
        raw_model_output=raw_output,
    )
    
    # Build charitable donation schema
    donation = CharitableDonationSchema(
        organization_name=parsed_data.get("organization_name")
        or parsed_data.get("provider")
        or "Unknown Organization",
        donation_date=parsed_data.get("donation_date") or parsed_data.get("service_date"),
        amount=float(parsed_data.get("amount", 0)),
        tax_deductible=parsed_data.get("tax_deductible") if parsed_data.get("tax_deductible") is not None else True,
        description=parsed_data.get("description"),
        raw_model_output=raw_output,
    )
    
    parsed_receipt = ParsedReceipt(
        suggested_category=inferred_category,
        expense=expense,
        charitable_data=donation,
        confidence=confidence,
        parsing_errors=[] if confidence > 0.7 else ["Low confidence in some fields"],
    )

    # Run duplicate check based on inferred category
    # Note: If MCP servers aren't configured, duplicate checks gracefully fail
    # and the error is surfaced to the user. Final duplicate checks still run at save time.
    duplicate_check_error: str | None = None
    duplicate_info: list[dict[str, Any]] = []
    is_duplicate = False

    if inferred_category == ExpenseCategory.HSA and expense.hsa_eligible:
        # HSA duplicate check
        definition = get_mcp_server_definitions(settings).get("hsa_ledger")
        if "hsa_ledger" not in enabled_mcp_servers:
            duplicate_check_error = (
                "HSA Ledger MCP server is disabled or not configured. Enable it and set required folder/sheet IDs to run duplicate checks."
            )
        elif not definition:
            duplicate_check_error = "HSA Ledger MCP server configuration is unavailable."
        else:
            mcp_client = MCPClient(
                definition.command,
                server_path_override=definition.server_path,
            )
            try:
                await mcp_client.start()
                dup_result = await mcp_client.check_for_duplicates(expense.model_dump())
                duplicate_info = [
                    dict(item)
                    for item in (dup_result.get("potential_duplicates") or [])
                    if isinstance(item, dict)
                ]
                is_duplicate = bool(dup_result.get("is_duplicate"))
                if dup_result.get("check_error"):
                    duplicate_check_error = str(dup_result.get("check_error"))

                tools_called.append(
                    {
                        "server_id": definition.id,
                        "tool_name": "check_for_duplicates",
                        "input": _json_compact({"expense_json": expense.model_dump()}),
                        "output": _json_compact(dup_result),
                    }
                )
            except Exception as dup_exc:
                duplicate_check_error = f"Duplicate check unavailable: {dup_exc}"
            finally:
                await mcp_client.stop()
    
    elif inferred_category == ExpenseCategory.CHARITABLE:
        # Charitable duplicate check
        definition = get_mcp_server_definitions(settings).get("charitable_ledger")
        if "charitable_ledger" not in enabled_mcp_servers:
            duplicate_check_error = (
                "Charitable Ledger MCP server is disabled or not configured. Enable it and set required folder/sheet IDs to run duplicate checks."
            )
        elif not definition:
            duplicate_check_error = "Charitable Ledger MCP server configuration is unavailable."
        else:
            mcp_client = MCPClient(
                definition.command,
                server_path_override=definition.server_path,
            )
            try:
                await mcp_client.start()
                dup_result = await mcp_client.check_charitable_duplicates(donation.model_dump())
                # Normalize charitable duplicates to match expected field names
                from vivian_api.routers.receipts import _normalize_charitable_duplicate
                raw_dups = dup_result.get("potential_duplicates") or []
                duplicate_info = [
                    _normalize_charitable_duplicate(item)
                    for item in raw_dups
                    if isinstance(item, dict)
                ]
                is_duplicate = bool(dup_result.get("is_duplicate"))
                if dup_result.get("check_error"):
                    duplicate_check_error = str(dup_result.get("check_error"))

                tools_called.append(
                    {
                        "server_id": definition.id,
                        "tool_name": "check_charitable_duplicates",
                        "input": _json_compact({"donation_json": donation.model_dump()}),
                        "output": _json_compact(dup_result),
                    }
                )
            except Exception as dup_exc:
                duplicate_check_error = f"Duplicate check unavailable: {dup_exc}"
            finally:
                await mcp_client.stop()

    # Build message based on category
    if inferred_category == ExpenseCategory.CHARITABLE:
        message = "Charitable receipt parsed. Review and confirm to finish importing it."
    elif not expense.hsa_eligible:
        message = "Receipt parsed as not HSA-eligible. Review before saving."
    else:
        message = "Receipt parsed. Review and confirm to finish importing it."

    return (
        DocumentWorkflowArtifact(
            attachment_id=attachment.attachment_id,
            document_type="receipt",
            status="ready_for_confirmation",
            message=message,
            temp_file_path=attachment.temp_file_path,
            filename=attachment.filename,
            parsed_data=parsed_receipt.model_dump(mode="json"),
            is_duplicate=is_duplicate,
            duplicate_info=duplicate_info,
            duplicate_check_error=duplicate_check_error,
        ),
        tools_called,
    )


def get_document_workflow_registry() -> dict[str, DocumentWorkflowHandler]:
    """Return document type -> workflow handler map."""
    return {
        "receipt": _run_receipt_workflow,
        "hsa_receipt": _run_receipt_workflow,         # backward compat
        "charitable_receipt": _run_receipt_workflow,  # backward compat
    }


async def execute_document_workflows(
    *,
    attachments: list[ChatAttachment],
    enabled_mcp_servers: list[str],
    settings: Settings,
) -> DocumentWorkflowExecution:
    """Execute workflow handlers for all uploaded chat attachments."""
    artifacts: list[DocumentWorkflowArtifact] = []
    tools_called: list[dict[str, str]] = []
    registry = get_document_workflow_registry()

    for attachment in attachments:
        handler = registry.get(attachment.document_type)
        if not handler:
            artifacts.append(
                DocumentWorkflowArtifact(
                    attachment_id=attachment.attachment_id,
                    document_type=attachment.document_type,
                    status="unsupported",
                    message=f"No workflow is registered for '{attachment.document_type}'.",
                    temp_file_path=attachment.temp_file_path,
                    filename=attachment.filename,
                )
            )
            continue

        artifact, handler_tools = await handler(
            attachment,
            enabled_mcp_servers,
            settings,
        )
        artifacts.append(artifact)
        tools_called.extend(handler_tools)

    if not artifacts:
        return DocumentWorkflowExecution(
            response_text="No attachments were provided.",
            artifacts=[],
            tools_called=[],
        )

    if len(artifacts) == 1:
        artifact = artifacts[0]
        return DocumentWorkflowExecution(
            response_text=artifact.message,
            artifacts=artifacts,
            tools_called=tools_called,
        )

    lines = ["I processed your uploaded documents:"]
    for artifact in artifacts:
        label = artifact.filename or artifact.document_type
        lines.append(f"- **{label}**: {artifact.message}")

    return DocumentWorkflowExecution(
        response_text="\n".join(lines),
        artifacts=artifacts,
        tools_called=tools_called,
    )
