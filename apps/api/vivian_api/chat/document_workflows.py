"""Document workflow routing for chat attachments."""

from __future__ import annotations

import json
import uuid
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, Field

from vivian_api.config import Settings
from vivian_api.services.mcp_client import MCPClient
from vivian_api.services.mcp_registry import get_mcp_server_definitions
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_shared.models import ExpenseSchema, ParsedReceipt


DocumentType = Literal["hsa_receipt", "charitable_receipt"]


class ChatAttachment(BaseModel):
    """Attachment metadata sent from chat clients."""

    attachment_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:10]}")
    document_type: DocumentType = "hsa_receipt"
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


async def _run_hsa_receipt_workflow(
    attachment: ChatAttachment,
    enabled_mcp_servers: list[str],
    settings: Settings,
) -> tuple[DocumentWorkflowArtifact, list[dict[str, str]]]:
    """Parse an HSA receipt and prepare data for confirmation UI."""
    tools_called: list[dict[str, str]] = []
    parser = OpenRouterService()

    try:
        parse_result = await parser.parse_receipt(attachment.temp_file_path)
    except Exception as exc:
        return (
            DocumentWorkflowArtifact(
                attachment_id=attachment.attachment_id,
                document_type=attachment.document_type,
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
                document_type=attachment.document_type,
                status="parse_error",
                message=str(parse_result.get("error", "Failed to parse receipt.")),
                temp_file_path=attachment.temp_file_path,
                filename=attachment.filename,
            ),
            tools_called,
        )

    parsed_data = parse_result.get("parsed_data") or {}
    confidence = _compute_receipt_confidence(parsed_data)
    raw_output = str(parse_result.get("raw_output", ""))

    expense = ExpenseSchema(
        provider=parsed_data.get("provider", "Unknown Provider"),
        service_date=parsed_data.get("service_date"),
        paid_date=parsed_data.get("paid_date"),
        amount=float(parsed_data.get("amount", 0)),
        hsa_eligible=bool(parsed_data.get("hsa_eligible", True)),
        raw_model_output=raw_output,
    )
    parsed_receipt = ParsedReceipt(
        expense=expense,
        confidence=confidence,
        parsing_errors=[] if confidence > 0.7 else ["Low confidence in some fields"],
    )

    duplicate_check_error: str | None = None
    duplicate_info: list[dict[str, Any]] = []
    is_duplicate = False

    definition = get_mcp_server_definitions(settings).get("vivian_hsa")
    if expense.hsa_eligible:
        if "vivian_hsa" not in enabled_mcp_servers:
            duplicate_check_error = (
                "Vivian HSA MCP server is disabled in this chat. Enable it to run duplicate checks."
            )
        elif not definition:
            duplicate_check_error = "Vivian HSA MCP server configuration is unavailable."
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

    message = "Receipt parsed. Review and confirm to finish importing it."
    if not expense.hsa_eligible:
        message = "Receipt parsed as not HSA-eligible. Review before saving."

    return (
        DocumentWorkflowArtifact(
            attachment_id=attachment.attachment_id,
            document_type=attachment.document_type,
            status="ready_for_confirmation",
            message=message,
            temp_file_path=attachment.temp_file_path,
            filename=attachment.filename,
            parsed_data=parsed_receipt.model_dump(),
            is_duplicate=is_duplicate,
            duplicate_info=duplicate_info,
            duplicate_check_error=duplicate_check_error,
        ),
        tools_called,
    )


async def _run_charitable_receipt_workflow(
    attachment: ChatAttachment,
    _enabled_mcp_servers: list[str],
    _settings: Settings,
) -> tuple[DocumentWorkflowArtifact, list[dict[str, str]]]:
    """Placeholder for future charitable receipt flow."""
    return (
        DocumentWorkflowArtifact(
            attachment_id=attachment.attachment_id,
            document_type=attachment.document_type,
            status="unsupported",
            message=(
                "Charitable receipt workflows are not implemented yet. "
                "This upload is ready for future support."
            ),
            temp_file_path=attachment.temp_file_path,
            filename=attachment.filename,
        ),
        [],
    )


def get_document_workflow_registry() -> dict[str, DocumentWorkflowHandler]:
    """Return document type -> workflow handler map."""
    return {
        "hsa_receipt": _run_hsa_receipt_workflow,
        "charitable_receipt": _run_charitable_receipt_workflow,
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
