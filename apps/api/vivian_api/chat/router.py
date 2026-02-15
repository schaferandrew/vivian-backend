"""Chat WebSocket router."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import httpx
import re

from vivian_api.chat.connection import connection_manager
from vivian_api.chat.document_workflows import (
    ChatAttachment,
    DocumentWorkflowArtifact,
    execute_document_workflows,
)
from vivian_api.chat.session import session_manager
from vivian_api.chat.handler import chat_handler
from vivian_api.chat.message_protocol import ChatMessage
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.llm import (
    get_chat_completion,
    get_chat_completion_result,
    LLMToolCall,
    ModelToolCallingUnsupportedError,
    OpenRouterCreditsError,
    OpenRouterRateLimitError,
    OllamaTimeoutError,
    OllamaConnectionError,
)
from vivian_api.config import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    Settings,
    check_ollama_status,
    get_selected_model,
    set_selected_model,
)
from vivian_api.auth.dependencies import CurrentUserContext, get_current_user_context
from vivian_api.db.database import get_db
from vivian_api.repositories.connection_repository import McpServerSettingsRepository
from vivian_api.repositories import ChatMessageRepository, ChatRepository
from vivian_api.services.mcp_client import MCPClient, MCPClientError
from vivian_api.services.mcp_registry import get_mcp_server_definitions, normalize_enabled_server_ids


router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)
ENABLED_SERVERS_PREFS_KEY = "__enabled_servers__"


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    chat_id: str | None = None
    web_search_enabled: bool = False
    enabled_mcp_servers: list[str] | None = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    session_id: str
    chat_id: str
    tools_called: list[dict[str, str]] = Field(default_factory=list)
    document_workflows: list[DocumentWorkflowArtifact] = Field(default_factory=list)


class ModelSelectRequest(BaseModel):
    model_id: str


settings = Settings()

SUMMARY_MODEL_ID = "google/gemini-3-flash-preview"
SUMMARY_REFINEMENT_MIN_MESSAGES = 4
MAX_MODEL_TOOL_ROUNDS = 4

MODEL_MCP_TOOL_SPECS: dict[str, dict[str, Any]] = {
    "get_unreimbursed_balance": {
        "server_id": "hsa_ledger",
        "description": "Return the current total unreimbursed HSA amount and count of unreimbursed expenses.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    "read_ledger_entries": {
        "server_id": "hsa_ledger",
        "description": (
            "Read HSA ledger entries with optional year/status filters and AND-based column predicates. "
            "Use this for summaries and transaction breakdowns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Optional calendar year, for example 2026."},
                "status_filter": {
                    "type": "string",
                    "enum": ["reimbursed", "unreimbursed", "not_hsa_eligible"],
                    "description": "Optional reimbursement status filter.",
                },
                "limit": {"type": "integer", "description": "Maximum rows to read (default 1000)."},
                "column_filters": {
                    "type": "array",
                    "description": (
                        "Optional AND filters by column. Operators: equals, not_equals, contains, "
                        "starts_with, ends_with, in, gt, gte, lt, lte."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": [
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
                                ],
                                "default": "equals",
                            },
                            "value": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "array", "items": {"type": "number"}},
                                ]
                            },
                            "case_sensitive": {"type": "boolean", "default": False},
                        },
                        "required": ["column", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    "get_charitable_summary": {
        "server_id": "charitable_ledger",
        "description": (
            "Return charitable donation totals and organization breakdowns, optionally scoped by tax year "
            "and column predicates. Prefer this for fast totals."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tax_year": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                    "description": "Optional tax year, for example 2026.",
                },
                "column_filters": {
                    "type": "array",
                    "description": (
                        "Optional AND filters by column. Operators: equals, not_equals, contains, "
                        "starts_with, ends_with, in, gt, gte, lt, lte."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": [
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
                                ],
                                "default": "equals",
                            },
                            "value": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "array", "items": {"type": "number"}},
                                ]
                            },
                            "case_sensitive": {"type": "boolean", "default": False},
                        },
                        "required": ["column", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    "read_charitable_ledger_entries": {
        "server_id": "charitable_ledger",
        "description": (
            "Read charitable ledger entries with optional tax-year, organization, tax-deductible, and "
            "column-level filters. Use this for filtered lists and detailed breakdowns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tax_year": {
                    "anyOf": [{"type": "string"}, {"type": "integer"}],
                    "description": "Optional tax year, for example 2026.",
                },
                "organization": {
                    "type": "string",
                    "description": "Optional case-insensitive organization name contains filter.",
                },
                "tax_deductible": {
                    "type": "boolean",
                    "description": "Optional deductible-only filter.",
                },
                "limit": {"type": "integer", "description": "Maximum rows to read (default 1000)."},
                "column_filters": {
                    "type": "array",
                    "description": (
                        "Optional AND filters by column. Operators: equals, not_equals, contains, "
                        "starts_with, ends_with, in, gt, gte, lt, lte."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {
                                "type": "string",
                                "enum": [
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
                                ],
                                "default": "equals",
                            },
                            "value": {
                                "anyOf": [
                                    {"type": "string"},
                                    {"type": "number"},
                                    {"type": "boolean"},
                                    {"type": "array", "items": {"type": "string"}},
                                    {"type": "array", "items": {"type": "number"}},
                                ]
                            },
                            "case_sensitive": {"type": "boolean", "default": False},
                        },
                        "required": ["column", "value"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
}


def _normalize_title(raw: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9\s'-]", " ", (raw or "")).strip()
    if not cleaned:
        cleaned = re.sub(r"[^A-Za-z0-9\s'-]", " ", (fallback or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "New Chat"

    words = cleaned.split()
    title = " ".join(words[:10]).strip()
    title = title[:72].strip()
    if not title:
        return "New Chat"
    return title[0].upper() + title[1:] if len(title) > 1 else title.upper()


def _is_low_signal_user_message(text: str) -> bool:
    message = (text or "").strip().lower()
    if not message:
        return True

    message = re.sub(r"\s+", " ", message)
    low_signal_patterns = (
        r"^(thanks|thank you|thx)\b",
        r"^(great|awesome|perfect|cool|nice)\b",
        r"^(sounds good|that works|got it|understood)\b",
        r"^(let'?s talk|let us talk)\b",
        r"^(anything else|what next)\b",
    )
    return any(re.search(pattern, message) for pattern in low_signal_patterns)


def _select_intent_anchor(user_messages: list[str]) -> str:
    """Pick a user message that captures intent, skipping pleasantries/closures."""
    candidates = [msg.strip() for msg in user_messages if msg and msg.strip()]
    if not candidates:
        return ""

    substantive = [msg for msg in candidates if not _is_low_signal_user_message(msg)]
    if substantive:
        # Use latest substantive ask to reflect the active task while avoiding end-of-chat thanks.
        return substantive[-1]

    return candidates[-1]


def _build_initial_title_from_first_user_message(message: str) -> str:
    source = (message or "").strip()
    if not source:
        return "New Chat"

    # Remove common conversational lead-ins so titles start with user intent.
    source = re.sub(
        r"^\s*(hi|hello|hey|yo)\b[\s,!.-]*",
        "",
        source,
        flags=re.IGNORECASE,
    )
    source = re.sub(
        r"^\s*(can you|could you|would you|please|help me|i need|i want to|i just)\b[\s,:-]*",
        "",
        source,
        flags=re.IGNORECASE,
    )

    return _normalize_title(source, message)


def _extract_addition_operands(message: str) -> tuple[float, float] | None:
    """Extract operands from simple addition prompts like '2+2' or 'add 2 and 2'."""
    plus_pattern = re.search(r"(-?\d+(?:\.\d+)?)\s*\+\s*(-?\d+(?:\.\d+)?)", message)
    if plus_pattern:
        return float(plus_pattern.group(1)), float(plus_pattern.group(2))

    add_pattern = re.search(
        r"\badd\s+(-?\d+(?:\.\d+)?)\s+(?:and|to)\s+(-?\d+(?:\.\d+)?)\b",
        message,
        flags=re.IGNORECASE,
    )
    if add_pattern:
        return float(add_pattern.group(1)), float(add_pattern.group(2))

    return None


def _format_number_for_display(value: float) -> str:
    """Render whole numbers without trailing .0 while preserving decimals."""
    if float(value).is_integer():
        return str(int(value))
    return format(value, "g")


def _get_default_home_id(current_user: CurrentUserContext) -> str:
    """Get the user's default home ID."""
    if not current_user.default_membership:
        raise HTTPException(status_code=400, detail="No home membership found")
    return current_user.default_membership.home_id


def _compact_json(value: object) -> str:
    """Serialize values for tools_called metadata."""
    try:
        return json.dumps(value, separators=(",", ":"), default=str)
    except Exception:
        return str(value)


def _is_balance_query(message: str) -> bool:
    """Detect natural language balance queries."""
    text = (message or "").strip().lower()
    if not text:
        return False

    patterns = (
        r"\bwhat(?:'s| is)?\s+(?:my\s+)?(?:hsa\s+)?balance\b",
        r"\bhow much\b.{0,30}\b(?:reimburse|reimbursed|unreimbursed|balance)\b",
        r"\b(?:hsa\s+)?unreimbursed\b.{0,20}\b(?:amount|balance|total)\b",
        r"\b(?:available|left)\b.{0,30}\b(?:reimburse|claim)\b",
        r"\bhow much can i reimburse\b",
        r"\bbalance check\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_hsa_summary_query(message: str) -> bool:
    """Detect HSA summary queries that should read ledger summary."""
    text = (message or "").strip().lower()
    if not text:
        return False

    patterns = (
        r"\bsummary\b.{0,30}\b(hsa|expense|expenses|ledger)\b",
        r"\bsummar(?:y|ize)\b.{0,30}\b(hsa|expense|expenses|ledger)\b",
        r"\b(hsa|ledger)\b.{0,30}\bsummary\b",
        r"\btotal\b.{0,30}\b(hsa|expenses|reimbursed|unreimbursed)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_explicit_hsa_tool_request(message: str) -> bool:
    """Detect explicit request to use HSA tool/server."""
    text = (message or "").strip().lower()
    if not text:
        return False
    markers = (
        "using my hsa tool",
        "use my hsa tool",
        "use hsa tool",
        "hsa_ledger",
        "hsa ledger tool",
    )
    return any(marker in text for marker in markers)


def _is_explicit_charitable_tool_request(message: str) -> bool:
    """Detect explicit request to use charitable tool/server."""
    text = (message or "").strip().lower()
    if not text:
        return False
    markers = (
        "using my charitable tool",
        "use my charitable tool",
        "charitable_ledger",
        "charitable ledger tool",
        "donation tool",
    )
    return any(marker in text for marker in markers)


def _is_balance_details_followup(message: str) -> bool:
    """Detect follow-ups that request balance details."""
    text = (message or "").strip().lower()
    if not text:
        return False

    patterns = (
        r"^\s*show(?: me)?\s+(?:the\s+)?(?:details|breakdown|entries|expenses)\s*$",
        r"^\s*(details|breakdown)\s*$",
        r"\bshow\b.{0,20}\b(?:details|breakdown|entries|expenses)\b",
        r"\blist\b.{0,20}\b(?:entries|expenses)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_flow_closure(message: str) -> bool:
    """Detect short acknowledgements that should close a lightweight flow."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return False
    return bool(
        re.fullmatch(
            r"(thanks|thank you|thx|done|all done|that'?s all|no thanks|no thank you)[!.]?",
            text,
        )
    )


def _in_recent_balance_context(session) -> bool:
    """Check if we should treat a message as balance follow-up."""
    if session.context.last_intent != "balance_query":
        return False

    ref_time = session.context.last_balance_query_time or session.context.last_balance_query
    if ref_time is None:
        return bool(session.context.last_balance_query_result or session.context.last_balance_result)

    return datetime.utcnow() - ref_time <= timedelta(minutes=30)


def _record_balance_context(session, result: dict) -> None:
    """Persist latest balance context for follow-up handling."""
    now = datetime.utcnow()
    session.context.last_balance_query = now
    session.context.last_balance_result = result
    session.context.last_balance_query_time = now
    session.context.last_balance_query_result = result
    session.context.last_intent = "balance_query"


async def _create_chat_mcp_client(
    *,
    mcp_server_id: str,
    db: Session,
    home_id: str,
) -> MCPClient:
    """Create MCP client for chat path using DB-backed configuration."""
    definitions = get_mcp_server_definitions(settings)
    definition = definitions.get(mcp_server_id)
    if not definition:
        raise ValueError(f"Unknown MCP server: {mcp_server_id}")

    return await MCPClient.from_db(
        server_command=definition.command,
        home_id=home_id,
        mcp_server_id=mcp_server_id,
        db=db,
        server_path_override=definition.server_path,
    )


def _resolve_enabled_mcp_servers_for_chat(
    *,
    requested_ids: list[str] | None,
    current_user: CurrentUserContext,
    db: Session,
) -> list[str]:
    """Resolve effective enabled MCP servers (chat override -> persisted defaults)."""
    if requested_ids is not None:
        return normalize_enabled_server_ids(requested_ids, settings)

    home_id = _get_default_home_id(current_user)
    settings_repo = McpServerSettingsRepository(db)
    prefs = settings_repo.get_by_home_and_server(home_id, ENABLED_SERVERS_PREFS_KEY)
    if prefs:
        raw_ids = prefs.settings_json.get("enabled_server_ids")
        if isinstance(raw_ids, list):
            return normalize_enabled_server_ids([str(server_id) for server_id in raw_ids], settings)
    return normalize_enabled_server_ids(None, settings)


def _build_mcp_tool_guidance(enabled_servers: list[str]) -> list[str]:
    """Build concise tool guidance for the model from MCP registry metadata."""
    definitions = get_mcp_server_definitions(settings)
    guidance: list[str] = []
    for server_id in enabled_servers:
        definition = definitions.get(server_id)
        if not definition:
            continue
        guidance.append(
            f"{server_id} tools available: {', '.join(definition.tools)}"
        )
        if server_id == "hsa_ledger":
            guidance.append(
                "For HSA summaries, use read_ledger_entries(year?, status_filter?, limit?, column_filters?). "
                "column_filters items use {column, operator, value, case_sensitive?}."
            )
        if server_id == "charitable_ledger":
            guidance.append(
                "For charitable totals, use get_charitable_summary(tax_year?, column_filters?). "
                "For filtered donation lists/details, use read_charitable_ledger_entries("
                "tax_year?, organization?, tax_deductible?, limit?, column_filters?). "
                "column_filters items use {column, operator, value, case_sensitive?}."
            )
    return guidance


def _build_model_tool_schema(enabled_servers: list[str]) -> list[dict[str, Any]]:
    """Build model-facing function schemas for enabled read/query MCP tools."""
    tool_schema: list[dict[str, Any]] = []
    for tool_name, spec in MODEL_MCP_TOOL_SPECS.items():
        if spec["server_id"] not in enabled_servers:
            continue
        tool_schema.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
        )
    return tool_schema


def _extract_mcp_result_text(result: dict[str, Any]) -> str:
    """Extract text payload from an MCP call_tool response."""
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return "{}"
    first = content[0]
    if not isinstance(first, dict):
        return "{}"
    text = first.get("text")
    if isinstance(text, str):
        return text
    return str(text) if text is not None else "{}"


def _coerce_model_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool arguments from model outputs to match MCP tool schemas."""
    if tool_name == "get_unreimbursed_balance":
        return {}

    if tool_name == "read_ledger_entries":
        normalized: dict[str, Any] = {}
        year = arguments.get("year")
        if isinstance(year, str) and year.strip().isdigit():
            normalized["year"] = int(year.strip())
        elif isinstance(year, int):
            normalized["year"] = year

        status = arguments.get("status_filter")
        if isinstance(status, str) and status.strip():
            normalized["status_filter"] = status.strip()

        limit = arguments.get("limit")
        if isinstance(limit, str) and limit.strip().isdigit():
            normalized["limit"] = int(limit.strip())
        elif isinstance(limit, (int, float)):
            normalized["limit"] = int(limit)

        column_filters = arguments.get("column_filters")
        if isinstance(column_filters, list):
            normalized["column_filters"] = column_filters
        return normalized

    if tool_name == "get_charitable_summary":
        normalized = {}
        tax_year = arguments.get("tax_year")
        if isinstance(tax_year, int):
            normalized["tax_year"] = str(tax_year)
        elif isinstance(tax_year, str) and tax_year.strip():
            normalized["tax_year"] = tax_year.strip()

        column_filters = arguments.get("column_filters")
        if isinstance(column_filters, list):
            normalized["column_filters"] = column_filters
        return normalized

    if tool_name == "read_charitable_ledger_entries":
        normalized: dict[str, Any] = {}
        tax_year = arguments.get("tax_year")
        if isinstance(tax_year, int):
            normalized["tax_year"] = str(tax_year)
        elif isinstance(tax_year, str) and tax_year.strip():
            normalized["tax_year"] = tax_year.strip()

        organization = arguments.get("organization")
        if isinstance(organization, str) and organization.strip():
            normalized["organization"] = organization.strip()

        tax_deductible = arguments.get("tax_deductible")
        if isinstance(tax_deductible, bool):
            normalized["tax_deductible"] = tax_deductible
        elif isinstance(tax_deductible, str):
            lowered = tax_deductible.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                normalized["tax_deductible"] = True
            elif lowered in {"false", "0", "no", "n"}:
                normalized["tax_deductible"] = False

        limit = arguments.get("limit")
        if isinstance(limit, str) and limit.strip().isdigit():
            normalized["limit"] = int(limit.strip())
        elif isinstance(limit, (int, float)):
            normalized["limit"] = int(limit)

        column_filters = arguments.get("column_filters")
        if isinstance(column_filters, list):
            normalized["column_filters"] = column_filters
        return normalized

    return arguments


def _parse_tool_result_payload(raw_text: str) -> dict[str, Any] | None:
    """Best-effort parse of tool result text as JSON object."""
    try:
        parsed = json.loads(raw_text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_output_for_metadata(raw_text: str) -> str:
    """Compact tool output for metadata persistence."""
    parsed = _parse_tool_result_payload(raw_text)
    if parsed is not None:
        return _compact_json(parsed)
    return raw_text


def _record_context_from_model_tool_result(session, tool_name: str, raw_text: str) -> None:
    """Update session context using model tool call results for follow-up handling."""
    payload = _parse_tool_result_payload(raw_text)
    if payload is None:
        return

    if tool_name == "get_unreimbursed_balance":
        if "total_unreimbursed" in payload:
            _record_balance_context(session, payload)
        return

    if tool_name == "read_ledger_entries":
        if payload.get("success"):
            _record_balance_context(
                session,
                {"summary": payload.get("summary", {}), "mode": "summary"},
            )
        return

    if tool_name == "get_charitable_summary" and payload.get("success"):
        _record_charitable_context(session, payload)
        return

    if tool_name == "read_charitable_ledger_entries" and payload.get("success"):
        summary_payload = payload.get("summary")
        if isinstance(summary_payload, dict):
            _record_charitable_context(
                session,
                {
                    "success": True,
                    "tax_year": payload.get("tax_year"),
                    "total": summary_payload.get("total_amount", 0),
                    "tax_deductible_total": summary_payload.get("tax_deductible_total", 0),
                    "by_organization": summary_payload.get("by_organization", {}),
                    "by_year": summary_payload.get("by_year", {}),
                },
            )
        else:
            _record_charitable_context(session, payload)


async def _execute_model_tool_call(
    *,
    tool_call: LLMToolCall,
    current_user: CurrentUserContext,
    db: Session,
    enabled_mcp_servers: list[str],
    mcp_clients: dict[str, MCPClient],
) -> tuple[str, dict[str, str]]:
    """Execute one model-emitted tool call against the mapped MCP server."""
    spec = MODEL_MCP_TOOL_SPECS.get(tool_call.name)
    if not spec:
        error_text = json.dumps({"success": False, "error": f"Unknown tool '{tool_call.name}'."})
        return (
            error_text,
            {
                "server_id": "unknown",
                "tool_name": tool_call.name,
                "input": _compact_json(tool_call.arguments),
                "output": error_text,
            },
        )

    server_id = str(spec["server_id"])
    if server_id not in enabled_mcp_servers:
        error_text = json.dumps(
            {
                "success": False,
                "error": f"MCP server '{server_id}' is not enabled for this chat.",
            }
        )
        return (
            error_text,
            {
                "server_id": server_id,
                "tool_name": tool_call.name,
                "input": _compact_json(tool_call.arguments),
                "output": error_text,
            },
        )

    normalized_arguments = _coerce_model_tool_arguments(tool_call.name, tool_call.arguments)
    try:
        client = mcp_clients.get(server_id)
        if client is None:
            home_id = _get_default_home_id(current_user)
            client = await _create_chat_mcp_client(
                mcp_server_id=server_id,
                db=db,
                home_id=home_id,
            )
            await client.start()
            mcp_clients[server_id] = client

        result = await client.call_tool(tool_call.name, normalized_arguments)
        raw_text = _extract_mcp_result_text(result)
        return (
            raw_text,
            {
                "server_id": server_id,
                "tool_name": tool_call.name,
                "input": _compact_json(normalized_arguments),
                "output": _tool_output_for_metadata(raw_text),
            },
        )
    except Exception as exc:
        error_text = json.dumps(
            {
                "success": False,
                "error": str(exc),
                "tool": tool_call.name,
            }
        )
        return (
            error_text,
            {
                "server_id": server_id,
                "tool_name": tool_call.name,
                "input": _compact_json(normalized_arguments),
                "output": error_text,
            },
        )


async def _run_model_tool_loop(
    *,
    base_messages: list[dict[str, Any]],
    web_search_enabled: bool,
    session,
    current_user: CurrentUserContext,
    db: Session,
    enabled_mcp_servers: list[str],
) -> tuple[str, list[dict[str, str]]]:
    """Run model tool-calling loop: model -> tool_calls -> MCP -> model final response."""
    tools = _build_model_tool_schema(enabled_mcp_servers)
    if not tools:
        response_text = await get_chat_completion(
            base_messages,
            web_search_enabled=web_search_enabled,
        )
        return response_text, []

    messages = [dict(message) for message in base_messages]
    tools_called: list[dict[str, str]] = []
    mcp_clients: dict[str, MCPClient] = {}
    try:
        for round_idx in range(1, MAX_MODEL_TOOL_ROUNDS + 1):
            completion = await get_chat_completion_result(
                messages,
                web_search_enabled=web_search_enabled,
                tools=tools,
                tool_choice="auto",
            )
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": completion.content or "",
            }
            if completion.tool_calls:
                assistant_message["tool_calls"] = [
                    tool_call.as_openai_dict() for tool_call in completion.tool_calls
                ]
            messages.append(assistant_message)
            logger.warning(
                "chat.message model_tool_round=%s tool_calls=%s",
                round_idx,
                [tool_call.name for tool_call in completion.tool_calls],
            )

            if not completion.tool_calls:
                final_response = (completion.content or "").strip()
                if final_response:
                    return final_response, tools_called
                break

            for tool_call in completion.tool_calls:
                raw_tool_output, call_metadata = await _execute_model_tool_call(
                    tool_call=tool_call,
                    current_user=current_user,
                    db=db,
                    enabled_mcp_servers=enabled_mcp_servers,
                    mcp_clients=mcp_clients,
                )
                tools_called.append(call_metadata)
                _record_context_from_model_tool_result(session, tool_call.name, raw_tool_output)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": raw_tool_output,
                    }
                )
                logger.warning(
                    "chat.message tool_executed name=%s server_id=%s",
                    tool_call.name,
                    call_metadata.get("server_id"),
                )

        return (
            "I reached the tool-calling limit before finishing this request. Please ask again with the same details.",
            tools_called,
        )
    finally:
        for client in mcp_clients.values():
            try:
                await client.stop()
            except Exception:
                logger.exception("chat.message failed_stopping_mcp_client")


def _is_charitable_query(message: str) -> bool:
    """Detect charitable summary/list natural-language queries."""
    text = (message or "").strip().lower()
    if not text:
        return False
    patterns = (
        r"\b(total|sum)\b.{0,25}\b(giving|donation|donated|charitable)\b",
        r"\bhow much\b.{0,25}\b(donat|giving|charit)\b",
        r"\bsummary\b.{0,30}\b(charitable|giving|donation)\b",
        r"\blist\b.{0,25}\b(organizations|charities|donations)\b",
        r"\bwho\b.{0,25}\b(donated|given)\b",
        r"\bcharitable\b.{0,20}\b(summary|total|organizations)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_charitable_orgs_followup(message: str) -> bool:
    """Detect org-list follow-ups in a charitable flow."""
    text = (message or "").strip().lower()
    if not text:
        return False
    patterns = (
        r"^\s*(show|list)\s+(?:the\s+)?(?:organizations|charities)\s*$",
        r"^\s*organizations\s*$",
        r"\bwhich\b.{0,20}\b(organizations|charities)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _has_complex_charitable_filter_request(message: str) -> bool:
    """Detect charitable queries that likely need richer tool filters than deterministic routing."""
    text = (message or "").strip().lower()
    if not text:
        return False
    patterns = (
        r"\bto\s+(?!date\b)[a-z0-9][^,.!?]{1,60}",
        r"\b(only|except|excluding|include|between|over|under|greater than|less than|at least|at most)\b",
        r"\b(tax[- ]?deductible|non[- ]?deductible)\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _extract_tax_year(message: str) -> str | None:
    """Extract a 4-digit tax year if present."""
    match = re.search(r"\b(20\d{2})\b", message or "")
    if not match:
        return None
    year = match.group(1)
    return year if 2000 <= int(year) <= 2100 else None


def _is_year_only_message(message: str) -> bool:
    """Detect messages that only provide a year."""
    return bool(re.fullmatch(r"\s*20\d{2}\s*", message or ""))


def _is_dual_summary_query(message: str) -> bool:
    """Detect requests that ask for both HSA and charitable summaries."""
    text = (message or "").strip().lower()
    if not text:
        return False

    has_hsa = "hsa" in text
    has_charitable = any(token in text for token in ("charitable", "donation", "giving"))
    has_both = "both" in text

    if has_hsa and has_charitable:
        return True
    if has_both and (has_hsa or has_charitable):
        return True

    patterns = (
        r"\bboth\b.{0,30}\b(summary|summaries|totals|breakdown)\b",
        r"\bgive me both\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _is_dual_summary_followup(message: str, session) -> bool:
    """Detect shorthand follow-up like 'both' while in HSA/charitable context."""
    text = re.sub(r"\s+", " ", (message or "").strip().lower())
    if not text:
        return False
    if text not in {"both", "give me both", "both please"}:
        return False
    return session.context.last_intent in {"balance_query", "charitable_query"}


def _in_recent_charitable_context(session) -> bool:
    """Check if we should treat a message as charitable follow-up."""
    if session.context.last_intent != "charitable_query":
        return False
    ref_time = session.context.last_charitable_query_time
    if ref_time is None:
        return bool(session.context.last_charitable_query_result)
    return datetime.utcnow() - ref_time <= timedelta(minutes=30)


def _record_charitable_context(session, result: dict) -> None:
    """Persist charitable query result for follow-ups."""
    now = datetime.utcnow()
    session.context.last_charitable_query_time = now
    session.context.last_charitable_query_result = result
    session.context.last_intent = "charitable_query"


def _format_charitable_response(summary: dict, include_orgs: bool) -> str:
    """Render charitable totals and optional organization list."""
    tax_year = summary.get("tax_year")
    total = float(summary.get("total", 0) or 0)
    deductible = float(summary.get("tax_deductible_total", 0) or 0)
    scope = f"for {tax_year}" if tax_year else "across all available years"
    lines = [
        f"Your total charitable giving {scope} is **${total:.2f}**.",
        f"Tax-deductible total: **${deductible:.2f}**.",
    ]
    if include_orgs:
        by_org = summary.get("by_organization", {}) or {}
        if isinstance(by_org, dict) and by_org:
            lines.append("")
            lines.append("Organizations:")
            for name, data in sorted(by_org.items(), key=lambda item: float((item[1] or {}).get("total", 0)), reverse=True):
                amount = float((data or {}).get("total", 0) or 0)
                lines.append(f"• **{name}**: ${amount:.2f}")
        else:
            lines.append("")
            lines.append("No organization-level donation rows were found for that scope.")
    else:
        lines.append("")
        lines.append("If you want, ask me to list organizations for a breakdown.")
    return "\n".join(lines)


def _context_tax_year(session) -> str | None:
    """Read latest known charitable tax year from session context."""
    result = session.context.last_charitable_query_result or {}
    if not isinstance(result, dict):
        return None
    tax_year = result.get("tax_year")
    if isinstance(tax_year, str) and re.fullmatch(r"20\d{2}", tax_year):
        return tax_year
    return None


async def _try_dual_summary_tool_response(
    *,
    message: str,
    session,
    current_user: CurrentUserContext,
    db: Session,
    enabled_mcp_servers: list[str],
) -> tuple[str, list[dict[str, str]]] | None:
    """Handle requests that need both HSA and charitable summaries."""
    if not (_is_dual_summary_query(message) or _is_dual_summary_followup(message, session)):
        return None

    missing = [
        server_id
        for server_id in ("hsa_ledger", "charitable_ledger")
        if server_id not in enabled_mcp_servers
    ]
    if missing:
        return (
            f"I can fetch both once these MCP servers are enabled: {', '.join(missing)}.",
            [],
        )

    tax_year = _extract_tax_year(message) or _context_tax_year(session)
    home_id = _get_default_home_id(current_user)
    tools_called: list[dict[str, str]] = []
    hsa_summary: dict | None = None
    charitable_summary: dict | None = None
    hsa_error: str | None = None
    charitable_error: str | None = None

    try:
        hsa_client = await _create_chat_mcp_client(
            mcp_server_id="hsa_ledger",
            db=db,
            home_id=home_id,
        )
        await hsa_client.start()
        try:
            hsa_args: dict[str, object] = {"limit": 1000}
            if tax_year:
                hsa_args["year"] = int(tax_year)
            hsa_result = await hsa_client.call_tool("read_ledger_entries", hsa_args)
            hsa_text = hsa_result.get("content", [{}])[0].get("text", "{}")
            hsa_data = json.loads(hsa_text)
            tools_called.append(
                {
                    "server_id": "hsa_ledger",
                    "tool_name": "read_ledger_entries",
                    "input": _compact_json(hsa_args),
                    "output": _compact_json(hsa_data),
                }
            )
            if hsa_data.get("success"):
                hsa_summary = hsa_data.get("summary", {})
                _record_balance_context(session, {"summary": hsa_summary, "mode": "summary"})
            else:
                hsa_error = str(hsa_data.get("error", "unknown error"))
        finally:
            await hsa_client.stop()
    except Exception as exc:
        hsa_error = str(exc)

    try:
        charitable_client = await _create_chat_mcp_client(
            mcp_server_id="charitable_ledger",
            db=db,
            home_id=home_id,
        )
        await charitable_client.start()
        try:
            charitable_args = {"tax_year": tax_year} if tax_year else {}
            charitable_result = await charitable_client.call_tool("get_charitable_summary", charitable_args)
            charitable_text = charitable_result.get("content", [{}])[0].get("text", "{}")
            charitable_data = json.loads(charitable_text)
            tools_called.append(
                {
                    "server_id": "charitable_ledger",
                    "tool_name": "get_charitable_summary",
                    "input": _compact_json(charitable_args),
                    "output": _compact_json(charitable_data),
                }
            )
            if charitable_data.get("success"):
                charitable_summary = charitable_data
                _record_charitable_context(session, charitable_data)
            else:
                charitable_error = str(charitable_data.get("error", "unknown error"))
        finally:
            await charitable_client.stop()
    except Exception as exc:
        charitable_error = str(exc)

    sections: list[str] = []
    if hsa_summary is not None:
        total_amount = float(hsa_summary.get("total_amount", 0) or 0)
        reimbursed = float(hsa_summary.get("total_reimbursed", 0) or 0)
        unreimbursed = float(hsa_summary.get("total_unreimbursed", 0) or 0)
        sections.append(
            "HSA summary:\n"
            f"• Total logged: **${total_amount:.2f}**\n"
            f"• Reimbursed: **${reimbursed:.2f}**\n"
            f"• Unreimbursed: **${unreimbursed:.2f}**"
        )
    elif hsa_error:
        sections.append(f"HSA summary unavailable: {hsa_error}")

    if charitable_summary is not None:
        total = float(charitable_summary.get("total", 0) or 0)
        deductible = float(charitable_summary.get("tax_deductible_total", 0) or 0)
        scope = f"for {charitable_summary.get('tax_year')}" if charitable_summary.get("tax_year") else "across all years"
        sections.append(
            "Charitable summary:\n"
            f"• Total giving {scope}: **${total:.2f}**\n"
            f"• Tax-deductible: **${deductible:.2f}**"
        )
    elif charitable_error:
        sections.append(f"Charitable summary unavailable: {charitable_error}")

    if not sections:
        return ("I couldn't fetch either summary right now. Please try again.", tools_called)
    return ("\n\n".join(sections), tools_called)


def _format_balance_details_response(summary_data: dict) -> str:
    """Render a concise balance-details response."""
    total_entries = int(summary_data.get("total_entries", 0) or 0)
    total_amount = float(summary_data.get("total_amount", 0) or 0)
    unreimbursed = float(summary_data.get("total_unreimbursed", 0) or 0)
    reimbursed = float(summary_data.get("total_reimbursed", 0) or 0)
    not_eligible = float(summary_data.get("total_not_eligible", 0) or 0)
    count_unreimbursed = int(summary_data.get("count_unreimbursed", 0) or 0)

    return (
        "Here are your HSA ledger details:\n\n"
        f"• Total entries: **{total_entries}**\n"
        f"• Total tracked: **${total_amount:.2f}**\n"
        f"• Unreimbursed: **${unreimbursed:.2f}** ({count_unreimbursed} expense(s))\n"
        f"• Reimbursed: **${reimbursed:.2f}**\n"
        f"• Not HSA-eligible: **${not_eligible:.2f}**\n\n"
        "If you want, I can also filter this by year or reimbursement status."
    )


def _format_hsa_summary_response(summary_data: dict) -> str:
    """Render a concise HSA summary response."""
    total_entries = int(summary_data.get("total_entries", 0) or 0)
    total_amount = float(summary_data.get("total_amount", 0) or 0)
    unreimbursed = float(summary_data.get("total_unreimbursed", 0) or 0)
    reimbursed = float(summary_data.get("total_reimbursed", 0) or 0)
    not_eligible = float(summary_data.get("total_not_eligible", 0) or 0)
    return (
        "Here is your HSA expense summary:\n\n"
        f"• Total logged expenses: **${total_amount:.2f}** ({total_entries} entries)\n"
        f"• Total reimbursed: **${reimbursed:.2f}**\n"
        f"• Total unreimbursed: **${unreimbursed:.2f}**\n"
        f"• Not HSA-eligible: **${not_eligible:.2f}**\n\n"
        "If you want, I can also list the recent transactions."
    )


async def _try_balance_tool_response(
    *,
    message: str,
    session,
    current_user: CurrentUserContext,
    db: Session,
    enabled_mcp_servers: list[str],
) -> tuple[str, list[dict[str, str]]] | None:
    """Handle balance queries + follow-ups with deterministic MCP tool routing."""
    has_balance_context = _in_recent_balance_context(session)
    is_balance_query = _is_balance_query(message)
    is_hsa_summary_query = _is_hsa_summary_query(message)
    is_explicit_tool_request = _is_explicit_hsa_tool_request(message)
    is_details_followup = _is_balance_details_followup(message)
    is_closure = _is_flow_closure(message)

    if has_balance_context and is_closure:
        session.context.last_intent = None
        return ("Sounds good. Reach out anytime if you want to review your HSA numbers again.", [])

    should_handle_summary = is_hsa_summary_query or is_explicit_tool_request
    if not is_balance_query and not should_handle_summary and not (has_balance_context and is_details_followup):
        if has_balance_context:
            session.context.last_intent = None
        return None

    if "hsa_ledger" not in enabled_mcp_servers:
        return (
            "I can check that once your HSA Ledger MCP server is enabled in settings.",
            [],
        )

    home_id = _get_default_home_id(current_user)
    try:
        mcp_client = await _create_chat_mcp_client(
            mcp_server_id="hsa_ledger",
            db=db,
            home_id=home_id,
        )
        await mcp_client.start()
    except Exception as exc:
        return (f"I couldn't connect to your HSA ledger right now: {exc}", [])
    try:
        if should_handle_summary and not (has_balance_context and is_details_followup):
            details_payload = await mcp_client.call_tool(
                "read_ledger_entries",
                {"limit": 1000},
            )
            details_text = details_payload.get("content", [{}])[0].get("text", "{}")
            details_data = json.loads(details_text)
            if not details_data.get("success"):
                error = str(details_data.get("error", "unknown error"))
                return (
                    f"I couldn't fetch your HSA summary right now: {error}",
                    [
                        {
                            "server_id": "hsa_ledger",
                            "tool_name": "read_ledger_entries",
                            "input": _compact_json({"limit": 1000}),
                            "output": _compact_json(details_data),
                        }
                    ],
                )

            summary = details_data.get("summary", {})
            _record_balance_context(session, {"summary": summary, "mode": "summary"})
            return (
                _format_hsa_summary_response(summary),
                [
                    {
                        "server_id": "hsa_ledger",
                        "tool_name": "read_ledger_entries",
                        "input": _compact_json({"limit": 1000}),
                        "output": _compact_json(details_data),
                    }
                ],
            )

        if has_balance_context and is_details_followup:
            details_payload = await mcp_client.call_tool(
                "read_ledger_entries",
                {"status_filter": "unreimbursed", "limit": 1000},
            )
            details_text = details_payload.get("content", [{}])[0].get("text", "{}")
            details_data = json.loads(details_text)
            if not details_data.get("success"):
                error = str(details_data.get("error", "unknown error"))
                return (
                    f"I fetched your balance earlier, but couldn't load details right now: {error}",
                    [
                        {
                            "server_id": "hsa_ledger",
                            "tool_name": "read_ledger_entries",
                            "input": _compact_json({"status_filter": "unreimbursed", "limit": 1000}),
                            "output": _compact_json(details_data),
                        }
                    ],
                )

            summary = details_data.get("summary", {})
            _record_balance_context(session, {"summary": summary, "mode": "details"})
            return (
                _format_balance_details_response(summary),
                [
                    {
                        "server_id": "hsa_ledger",
                        "tool_name": "read_ledger_entries",
                        "input": _compact_json({"status_filter": "unreimbursed", "limit": 1000}),
                        "output": _compact_json(details_data),
                    }
                ],
            )

        result = await mcp_client.get_unreimbursed_balance()
        if "error" in result and "total_unreimbursed" not in result:
            error = str(result.get("error", "unknown error"))
            return (
                f"I couldn't fetch your HSA balance right now: {error}",
                [
                    {
                        "server_id": "hsa_ledger",
                        "tool_name": "get_unreimbursed_balance",
                        "input": "{}",
                        "output": _compact_json(result),
                    }
                ],
            )

        _record_balance_context(session, result)
        balance = float(result.get("total_unreimbursed", 0) or 0)
        count = int(result.get("count", 0) or 0)
        response = (
            f"Your current unreimbursed HSA balance is **${balance:.2f}** "
            f"across **{count}** expense(s).\n\n"
            "If you want, say **show details** for a ledger breakdown."
        )
        return (
            response,
            [
                {
                    "server_id": "hsa_ledger",
                    "tool_name": "get_unreimbursed_balance",
                    "input": "{}",
                    "output": _compact_json(result),
                }
            ],
        )
    finally:
        await mcp_client.stop()


async def _try_charitable_tool_response(
    *,
    message: str,
    session,
    current_user: CurrentUserContext,
    db: Session,
    enabled_mcp_servers: list[str],
) -> tuple[str, list[dict[str, str]]] | None:
    """Handle charitable summary/list requests with deterministic MCP routing."""
    has_context = _in_recent_charitable_context(session)
    is_query = _is_charitable_query(message)
    is_explicit_tool_request = _is_explicit_charitable_tool_request(message)
    is_orgs_followup = _is_charitable_orgs_followup(message)
    is_year_only_followup = has_context and _is_year_only_message(message)
    is_closure = _is_flow_closure(message)

    if has_context and is_closure:
        session.context.last_intent = None
        return ("Happy to help. Ask anytime if you want another giving summary.", [])

    should_handle_summary = is_query or is_explicit_tool_request or is_year_only_followup
    if not should_handle_summary and not (has_context and is_orgs_followup):
        if has_context:
            session.context.last_intent = None
        return None

    # Let the model tool loop handle richer filtered requests (organization, deductible-only, etc.).
    if should_handle_summary and _has_complex_charitable_filter_request(message):
        return None

    if "charitable_ledger" not in enabled_mcp_servers:
        return (
            "I can do that once your Charitable Ledger MCP server is enabled in settings.",
            [],
        )

    include_orgs = is_orgs_followup or is_year_only_followup or bool(
        re.search(r"\b(organization|organizations|charities|charity|who)\b", message, flags=re.IGNORECASE)
    )
    tax_year = _extract_tax_year(message) or (_context_tax_year(session) if has_context else None)
    home_id = _get_default_home_id(current_user)
    try:
        mcp_client = await _create_chat_mcp_client(
            mcp_server_id="charitable_ledger",
            db=db,
            home_id=home_id,
        )
        await mcp_client.start()
    except Exception as exc:
        return (f"I couldn't connect to your charitable ledger right now: {exc}", [])

    try:
        arguments = {"tax_year": tax_year} if tax_year else {}
        result = await mcp_client.call_tool("get_charitable_summary", arguments)
        content = result.get("content", [{}])[0].get("text", "{}")
        summary_data = json.loads(content)
        if not summary_data.get("success"):
            error = str(summary_data.get("error", "unknown error"))
            return (
                f"I couldn't fetch your charitable summary right now: {error}",
                [
                    {
                        "server_id": "charitable_ledger",
                        "tool_name": "get_charitable_summary",
                        "input": _compact_json(arguments),
                        "output": _compact_json(summary_data),
                    }
                ],
            )

        _record_charitable_context(session, summary_data)
        return (
            _format_charitable_response(summary_data, include_orgs=include_orgs),
            [
                {
                    "server_id": "charitable_ledger",
                    "tool_name": "get_charitable_summary",
                    "input": _compact_json(arguments),
                    "output": _compact_json(summary_data),
                }
            ],
        )
    finally:
        await mcp_client.stop()


async def _try_addition_tool_response(
    *,
    message: str,
    enabled_mcp_servers: list[str],
) -> tuple[str, list[dict[str, str]]] | None:
    """Use the test addition MCP tool when the message clearly asks for arithmetic."""
    lower_message = message.lower()
    explicit_tool_request = (
        "addition tool" in lower_message
        or "mcp-server" in lower_message
        or "mcp server" in lower_message
        or "add_numbers" in lower_message
    )

    if "test_addition" not in enabled_mcp_servers and not explicit_tool_request:
        return None

    operands = _extract_addition_operands(message)
    if not operands:
        return None

    settings_obj = Settings()
    definitions = get_mcp_server_definitions(settings_obj)
    definition = definitions.get("test_addition")
    if not definition or "add_numbers" not in definition.tools:
        return None

    a, b = operands
    mcp_client = MCPClient(
        definition.command,
        server_path_override=definition.server_path,
    )
    await mcp_client.start()
    try:
        result = await mcp_client.add_numbers(a, b)
        display_a = _format_number_for_display(a)
        display_b = _format_number_for_display(b)
        if not result.get("success"):
            error_message = str(result.get("error", "unknown error"))
            return (
                f"I tried the addition tool, but it failed: {error_message}",
                [
                    {
                        "server_id": definition.id,
                        "tool_name": "add_numbers",
                        "input": f"{display_a} + {display_b}",
                        "output": f"error: {error_message}",
                    }
                ],
            )

        sum_value = float(result.get("sum", a + b))
        display_sum = _format_number_for_display(sum_value)
        return (
            f"Using your addition tool: {display_a} + {display_b} = {display_sum}",
            [
                {
                    "server_id": definition.id,
                    "tool_name": "add_numbers",
                    "input": f"{display_a} + {display_b}",
                    "output": display_sum,
                }
            ],
        )
    except MCPClientError as exc:
        display_a = _format_number_for_display(a)
        display_b = _format_number_for_display(b)
        return (
            f"I tried the addition tool, but it failed: {exc}",
            [
                {
                    "server_id": definition.id,
                    "tool_name": "add_numbers",
                    "input": f"{display_a} + {display_b}",
                    "output": f"error: {exc}",
                }
            ],
        )
    finally:
        await mcp_client.stop()


async def generate_summary_from_messages(messages: list) -> tuple[str, str]:
    """Generate a concise chat title/summary from chat messages."""
    if not messages:
        return "New Chat", "New Chat"

    user_messages = [
        str(msg.get("content", "")).strip()
        for msg in messages
        if msg.get("role") == "user" and str(msg.get("content", "")).strip()
    ]
    first_user_message = user_messages[0] if user_messages else ""
    latest_user_message = user_messages[-1] if user_messages else ""
    anchor_user_message = _select_intent_anchor(user_messages)

    if not first_user_message:
        return "New Chat", "New Chat"

    content_preview = anchor_user_message[:180].replace("\n", " ")
    summary_source = anchor_user_message or first_user_message

    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "can", "do", "for", "from",
        "get", "give", "hello", "help", "hi", "how", "i", "in", "installed", "is",
        "it", "just", "let", "me", "my", "of", "on", "or", "our", "please", "set",
        "show", "test", "that", "the", "this", "to", "up", "we", "with", "you",
        "your", "thanks", "thank", "great", "updates", "talk",
    }

    keyword_priority = [
        "markdown", "blockquote", "rendering", "renderer", "test", "summary", "title",
        "chat", "session", "hsa", "contribution", "limit", "limits", "eligibility",
        "income", "magi", "receipt", "balance", "upload", "settings", "model",
    ]

    def keyword_fallback_title(primary: str, secondary: str = "") -> str:
        source = f"{primary} {secondary}".strip()
        tokens = re.findall(r"[A-Za-z0-9']+", source.lower())

        selected: list[str] = []
        for keyword in keyword_priority:
            if keyword in tokens and keyword not in selected:
                selected.append(keyword)
            if len(selected) >= 3:
                break

        if len(selected) < 6:
            for token in tokens:
                if token in stop_words or len(token) < 3 or token in selected:
                    continue
                selected.append(token)
                if len(selected) >= 6:
                    break

        if not selected:
            return "New Chat"

        words = selected[:6]
        return _normalize_title(" ".join(word.capitalize() for word in words), first_user_message)

    system_prompt = """You write chat list titles.

Rules:
- TITLE should be 2 to 6 words.
- TITLE must be specific to the user intent, not generic.
- Use plain words only (no quotes, no emoji).
- Prefer noun-heavy phrasing (what user wants), not conversational phrasing.
- Ignore pure acknowledgements/closures (for example: "thanks", "great updates", "let's talk").
- If troubleshooting display/formatting, name the concrete surface (e.g., "Markdown Rendering Test").
- SUMMARY should be 3 to 10 words and closely match TITLE.
- Use a substantive user ask as the anchor intent.
- Use the most recent 6 turns to refine specificity.

Example:
User asks about testing markdown rendering.
TITLE: Markdown Rendering Test
SUMMARY: Markdown rendering troubleshooting

Output format (must match exactly):
TITLE: <title>
SUMMARY: <summary>"""

    recent_messages = []
    for msg in messages:
        role = str(msg.get("role", "")).strip().lower()
        content = str(msg.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            recent_messages.append({"role": role, "content": content})
    recent_window = recent_messages[-6:]
    context_block = "\n".join(
        f"- {m['role']}: {m['content'][:240].replace(chr(10), ' ')}"
        for m in recent_window
    )
    user_prompt = (
        f"Conversation recent messages:\n{context_block}\n\n"
        f"First user message: {first_user_message}\n"
        f"Latest user message: {latest_user_message}\n\n"
        f"Anchor user message: {anchor_user_message}\n\n"
        "Generate the title and summary now."
    )

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.openrouter_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SUMMARY_MODEL_ID,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 100,
                    "temperature": 0.3,
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]

                title_raw = ""
                summary_raw = ""

                for line in content.split("\n"):
                    line_clean = line.strip()
                    if line_clean.upper().startswith("TITLE:"):
                        title_raw = line_clean.split(":", 1)[1].strip()
                    elif line_clean.upper().startswith("SUMMARY:"):
                        summary_raw = line_clean.split(":", 1)[1].strip()

                generated_title = _normalize_title(
                    title_raw or summary_raw or content_preview,
                    summary_source,
                )
                generated_summary = (
                    summary_raw.strip()
                    if summary_raw and summary_raw.strip()
                    else generated_title
                )
                generated_summary = re.sub(r"\s+", " ", generated_summary).strip()
                generated_summary = generated_summary[:160].strip() if generated_summary else generated_title

                weak_prefixes = ("i ", "i just", "hello", "hi ")
                if (
                    generated_title.lower() == "new chat"
                    or generated_title.lower().startswith(weak_prefixes)
                    or _is_low_signal_user_message(generated_title)
                ):
                    generated_title = keyword_fallback_title(summary_source, first_user_message)

                if (
                    not generated_summary
                    or generated_summary.lower() == "new chat"
                    or _is_low_signal_user_message(generated_summary)
                ):
                    generated_summary = generated_title

                return generated_title, generated_summary
            else:
                print(f"Summary generation failed: {response.text}")
                fallback = keyword_fallback_title(summary_source, first_user_message)
                return fallback, fallback
    except Exception as e:
        print(f"Error generating summary: {e}")
        fallback = keyword_fallback_title(summary_source, first_user_message)
        return fallback, fallback


@router.get("/models")
async def list_models(
    _current_user: CurrentUserContext = Depends(get_current_user_context),
):
    """List available OpenRouter models with provider status."""
    ollama_status = await check_ollama_status()
    
    providers = {
        "OpenAI": {"status": "available"},
        "Anthropic": {"status": "available"},
        "Google": {"status": "available"},
        "Ollama": ollama_status,
    }
    
    models_with_status = []
    for model in AVAILABLE_MODELS:
        model_info = {
            "id": model["id"],
            "name": model["name"],
            "provider": model["provider"],
            "selectable": True if model["provider"] != "Ollama" else ollama_status.get("available", False),
            "free": model.get("free", False)
        }
        models_with_status.append(model_info)
    
    return {
        "models": models_with_status,
        "providers": providers,
        "current_model": get_selected_model(),
        "default_model": DEFAULT_MODEL
    }


@router.post("/models/select")
async def select_model(
    request: ModelSelectRequest,
    _current_user: CurrentUserContext = Depends(get_current_user_context),
):
    """Change the active model (in-memory)."""
    ollama_status = await check_ollama_status()
    
    valid_ids = [m["id"] for m in AVAILABLE_MODELS]
    if request.model_id not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model ID. Available: {valid_ids}"
        )
    
    model = next((m for m in AVAILABLE_MODELS if m["id"] == request.model_id), None)
    if model and model["provider"] == "Ollama" and not ollama_status.get("available", False):
        raise HTTPException(
            status_code=503,
            detail="Ollama is not running. Please start Ollama to use this model."
        )
    
    set_selected_model(request.model_id)
    return {"success": True, "selected_model": get_selected_model()}


@router.post("/sessions")
async def create_session(
    _current_user: CurrentUserContext = Depends(get_current_user_context),
):
    """Create a new chat session."""
    session = session_manager.create_session()
    return JSONResponse({
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "message": "Session created successfully. Connect via WebSocket with this session_id."
    })


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    _current_user: CurrentUserContext = Depends(get_current_user_context),
):
    """Delete a chat session."""
    if session_manager.delete_session(session_id):
        return JSONResponse({
            "success": True,
            "message": f"Session {session_id} deleted"
        })
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    _current_user: CurrentUserContext = Depends(get_current_user_context),
):
    """Get session info."""
    session = session_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return JSONResponse({
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "last_activity_at": session.last_activity_at.isoformat(),
        "message_count": len(session.messages),
        "current_flow": session.current_flow.flow_type.value if session.current_flow else None,
        "is_active": session.websocket is not None
    })


@router.post("/message", response_model=ChatResponse)
async def chat_message(
    request: ChatRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db),
):
    """HTTP endpoint for chat messages using OpenRouter."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)

    # Resolve or create persistent chat independently from session state.
    db_chat = None
    if request.chat_id:
        db_chat = chat_repo.get(request.chat_id)
        if not db_chat:
            raise HTTPException(status_code=404, detail="Chat not found")
        if db_chat.user_id != current_user.user.id:
            raise HTTPException(status_code=404, detail="Chat not found")
    else:
        db_chat = chat_repo.create(
            user_id=current_user.user.id,
            title="New Chat",
            model=get_selected_model(),
        )

    # Get or create session (in-memory)
    if request.session_id:
        session = session_manager.get_session(request.session_id)
        if not session:
            session = session_manager.create_session(session_id=request.session_id)
    else:
        session = session_manager.create_session()

    effective_enabled_servers = _resolve_enabled_mcp_servers_for_chat(
        requested_ids=request.enabled_mcp_servers,
        current_user=current_user,
        db=db,
    )
    session.context.web_search_enabled = bool(request.web_search_enabled)
    session.context.enabled_mcp_servers = effective_enabled_servers
    attachment_metadata = [attachment.model_dump() for attachment in request.attachments]
    user_metadata = {"attachments": attachment_metadata} if attachment_metadata else None

    # Store user message in PostgreSQL if chat exists
    if db_chat:
        message_repo.create(
            chat_id=db_chat.id,
            role="user",
            content=request.message,
            metadata=user_metadata,
        )
        # Set an immediate first-pass title from the first user message.
        if (db_chat.title or "").strip().lower() == "new chat":
            try:
                db_messages = message_repo.list_for_chat(db_chat.id)
                if len(db_messages) == 1 and db_messages[0].role == "user":
                    initial_title = _build_initial_title_from_first_user_message(request.message)
                    chat_repo.update_title(db_chat.id, initial_title)
                    chat_repo.update_summary(db_chat.id, initial_title)
            except Exception as e:
                print(f"Error generating initial title: {e}")

    # Store user message in session (in-memory)
    session.context.web_search_enabled = bool(request.web_search_enabled)
    session.context.enabled_mcp_servers = effective_enabled_servers
    session.add_message(role="user", content=request.message, metadata=user_metadata)
    logger.warning(
        "chat.message session_id=%s chat_id=%s enabled_mcp_servers=%s message=%s",
        session.session_id,
        db_chat.id if db_chat else None,
        session.context.enabled_mcp_servers,
        request.message[:140].replace("\n", " "),
    )

    # Convert session messages to OpenRouter format; prepend system prompt so model stays in character
    messages = [
        {
            "role": "system",
            "content": VivianPersonality.get_system_prompt(
                current_date=datetime.now(timezone.utc).date().isoformat(),
                user_location=settings.user_location or None,
                enabled_mcp_servers=session.context.enabled_mcp_servers,
                mcp_tool_guidance=_build_mcp_tool_guidance(session.context.enabled_mcp_servers),
            ),
        },
        *(
            {"role": msg["role"], "content": msg["content"]}
            for msg in session.messages
        ),
    ]

    tools_called: list[dict[str, str]] = []
    document_workflows: list[DocumentWorkflowArtifact] = []
    if request.attachments:
        workflow_result = await execute_document_workflows(
            attachments=request.attachments,
            enabled_mcp_servers=session.context.enabled_mcp_servers,
            settings=settings,
        )
        response_text = workflow_result.response_text
        tools_called = workflow_result.tools_called
        document_workflows = workflow_result.artifacts
    else:
        tool_response = await _try_dual_summary_tool_response(
            message=request.message,
            session=session,
            current_user=current_user,
            db=db,
            enabled_mcp_servers=session.context.enabled_mcp_servers,
        )
        if not tool_response:
            tool_response = await _try_balance_tool_response(
                message=request.message,
                session=session,
                current_user=current_user,
                db=db,
                enabled_mcp_servers=session.context.enabled_mcp_servers,
            )
        if not tool_response:
            tool_response = await _try_charitable_tool_response(
                message=request.message,
                session=session,
                current_user=current_user,
                db=db,
                enabled_mcp_servers=session.context.enabled_mcp_servers,
            )
        if not tool_response:
            tool_response = await _try_addition_tool_response(
                message=request.message,
                enabled_mcp_servers=session.context.enabled_mcp_servers,
            )
        if tool_response:
            response_text, tools_called = tool_response
            logger.warning(
                "chat.message used deterministic tool routing session_id=%s tools_called=%s",
                session.session_id,
                [tool.get("tool_name") for tool in tools_called],
            )
        else:
            try:
                response_text, tools_called = await _run_model_tool_loop(
                    base_messages=messages,
                    web_search_enabled=request.web_search_enabled,
                    session=session,
                    current_user=current_user,
                    db=db,
                    enabled_mcp_servers=session.context.enabled_mcp_servers,
                )
            except ModelToolCallingUnsupportedError as e:
                logger.warning(
                    "chat.message model_tool_loop_unsupported model=%s error=%s",
                    get_selected_model(),
                    e.message,
                )
                response_text = await get_chat_completion(
                    messages,
                    web_search_enabled=request.web_search_enabled,
                )
            except OpenRouterCreditsError as e:
                # Handle model not found (404) errors vs insufficient credits (402) errors
                if "Model error" in e.message:
                    return JSONResponse(
                        status_code=404,
                        content={"error": "model_not_found", "message": e.message},
                    )
                return JSONResponse(
                    status_code=402,
                    content={"error": "insufficient_credits", "message": e.message},
                )
            except OpenRouterRateLimitError as e:
                return JSONResponse(
                    status_code=429,
                    content={"error": "rate_limit", "message": e.message},
                )
            except OllamaTimeoutError as e:
                print(f"Ollama timeout: {e}")
                return JSONResponse(
                    status_code=504,
                    content={"error": "ollama_timeout", "message": str(e)},
                )
            except OllamaConnectionError as e:
                print(f"Ollama connection error: {e}")
                return JSONResponse(
                    status_code=502,
                    content={"error": "ollama_unavailable", "message": str(e)},
                )
            except Exception as e:
                print(f"Error getting chat completion: {e}")
                import traceback
                traceback.print_exc()
                return JSONResponse(
                    status_code=500,
                    content={"error": "server_error", "message": str(e) or "An unexpected error occurred."},
                )

    # Store assistant response in PostgreSQL if chat exists
    assistant_metadata_payload: dict[str, object] = {}
    if tools_called:
        assistant_metadata_payload["tools_called"] = tools_called
    if document_workflows:
        assistant_metadata_payload["document_workflows"] = [
            workflow.model_dump(mode="json") for workflow in document_workflows
        ]
    assistant_metadata = assistant_metadata_payload or None
    if db_chat:
        message_repo.create(
            chat_id=db_chat.id,
            role="assistant",
            content=response_text,
            metadata=assistant_metadata,
        )

        # Refine title/summary once enough context exists (includes assistant responses).
        try:
            db_messages = message_repo.list_for_chat(db_chat.id)
            if len(db_messages) >= SUMMARY_REFINEMENT_MIN_MESSAGES:
                messages_dict = [msg.to_dict() for msg in db_messages]
                title, summary = await generate_summary_from_messages(messages_dict)

                if title:
                    chat_repo.update_title(db_chat.id, title)
                if summary:
                    chat_repo.update_summary(db_chat.id, summary)
        except Exception as e:
            print(f"Error generating summary: {e}")

    # Store assistant response in session (in-memory)
    session.add_message(role="assistant", content=response_text, metadata=assistant_metadata)

    return ChatResponse(
        response=response_text,
        session_id=session.session_id,
        chat_id=db_chat.id,
        tools_called=tools_called,
        document_workflows=document_workflows,
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for chat."""
    session = None
    try:
        # Accept connection and get/create session
        session = await connection_manager.connect(websocket)
        print(f"WebSocket connected: session {session.session_id}")
        
        # Send welcome/handshake
        await chat_handler._handle_handshake(session)
        print(f"Handshake sent for session {session.session_id}")
        
        while True:
            try:
                # Receive and parse message
                data = await websocket.receive_json()
                print(f"Received message: {data}")
                
                message = ChatMessage(**data)
                message.session_id = session.session_id
                
                # Handle the message
                await chat_handler.handle_message(session, message)

            except OpenRouterCreditsError as e:
                await connection_manager.send_error(
                    session,
                    error_id="insufficient_credits",
                    category="system_error",
                    severity="user_fixable",
                    message=e.message,
                    recovery_options=[{"id": "retry", "label": "Try again"}],
                )
            except OpenRouterRateLimitError as e:
                await connection_manager.send_error(
                    session,
                    error_id="rate_limit",
                    category="system_error",
                    severity="user_fixable",
                    message=e.message,
                    recovery_options=[{"id": "retry", "label": "Try again"}],
                )
            except Exception as e:
                print(f"Error handling message: {e}")
                import traceback
                traceback.print_exc()
                # Send error back to client
                await connection_manager.send_error(
                    session,
                    error_id=f"msg_error_{session.session_id}",
                    category="system_error",
                    severity="recoverable",
                    message=f"I couldn't process that message: {str(e)}",
                    recovery_options=[
                        {"id": "continue", "label": "Continue"}
                    ]
                )
                
    except WebSocketDisconnect:
        print(f"Client disconnected from session {session.session_id}")
        if session:
            connection_manager.disconnect(websocket)
    except Exception as e:
        print(f"WebSocket error: {e}")
        import traceback
        traceback.print_exc()
        if session:
            connection_manager.disconnect(websocket)
