"""Chat WebSocket router."""

from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import httpx
import re
from datetime import datetime, timezone

from vivian_api.chat.connection import connection_manager
from vivian_api.chat.session import session_manager
from vivian_api.chat.handler import chat_handler
from vivian_api.chat.message_protocol import ChatMessage
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.llm import get_chat_completion, OpenRouterCreditsError, OpenRouterRateLimitError
from vivian_api.config import (
    AVAILABLE_MODELS,
    DEFAULT_MODEL,
    Settings,
    check_ollama_status,
    get_selected_model,
    set_selected_model,
)
from vivian_api.db.database import get_db
from vivian_api.repositories import ChatMessageRepository, ChatRepository
from vivian_api.services.mcp_client import MCPClient, MCPClientError
from vivian_api.services.mcp_registry import get_mcp_server_definitions, normalize_enabled_server_ids


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    chat_id: str | None = None
    web_search_enabled: bool = False
    enabled_mcp_servers: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    session_id: str
    chat_id: str
    tools_called: list[dict[str, str]] = Field(default_factory=list)


class ModelSelectRequest(BaseModel):
    model_id: str


settings = Settings()

SUMMARY_MODEL_ID = "google/gemini-3-flash-preview"
SUMMARY_REFINEMENT_MIN_MESSAGES = 4


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
async def list_models():
    """List available OpenRouter models with provider status."""
    ollama_status = check_ollama_status()
    
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
async def select_model(request: ModelSelectRequest):
    """Change the active model (in-memory)."""
    ollama_status = check_ollama_status()
    
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
async def create_session():
    """Create a new chat session."""
    session = session_manager.create_session()
    return JSONResponse({
        "session_id": session.session_id,
        "created_at": session.created_at.isoformat(),
        "message": "Session created successfully. Connect via WebSocket with this session_id."
    })


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a chat session."""
    if session_manager.delete_session(session_id):
        return JSONResponse({
            "success": True,
            "message": f"Session {session_id} deleted"
        })
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
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
async def chat_message(request: ChatRequest, db: Session = Depends(get_db)):
    """HTTP endpoint for chat messages using OpenRouter."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)

    # Resolve or create persistent chat independently from session state.
    db_chat = None
    if request.chat_id:
        db_chat = chat_repo.get(request.chat_id)
        if not db_chat:
            raise HTTPException(status_code=404, detail="Chat not found")
    else:
        db_chat = chat_repo.create(title="New Chat", model=get_selected_model())

    # Get or create session (in-memory)
    if request.session_id:
        session = session_manager.get_session(request.session_id)
        if not session:
            session = session_manager.create_session(session_id=request.session_id)
    else:
        session = session_manager.create_session()

    session.context.web_search_enabled = bool(request.web_search_enabled)
    session.context.enabled_mcp_servers = normalize_enabled_server_ids(
        request.enabled_mcp_servers,
        settings,
    )

    # Store user message in PostgreSQL if chat exists
    if db_chat:
        message_repo.create(chat_id=db_chat.id, role="user", content=request.message)
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
    session.context.enabled_mcp_servers = normalize_enabled_server_ids(
        request.enabled_mcp_servers,
        settings,
    )
    session.add_message(role="user", content=request.message)

    # Convert session messages to OpenRouter format; prepend system prompt so model stays in character
    messages = [
        {
            "role": "system",
            "content": VivianPersonality.get_system_prompt(
                current_date=datetime.now(timezone.utc).date().isoformat(),
                user_location=settings.user_location or None,
                enabled_mcp_servers=session.context.enabled_mcp_servers,
            ),
        },
        *(
            {"role": msg["role"], "content": msg["content"]}
            for msg in session.messages
        ),
    ]

    tools_called: list[dict[str, str]] = []
    tool_response = await _try_addition_tool_response(
        message=request.message,
        enabled_mcp_servers=session.context.enabled_mcp_servers,
    )
    if tool_response:
        response_text, tools_called = tool_response
    else:
        # Get response from OpenRouter
        # Use request's web_search_enabled setting (default False to avoid unexpected costs)
        try:
            response_text = await get_chat_completion(messages, web_search_enabled=request.web_search_enabled)
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
        except Exception as e:
            print(f"Error getting chat completion: {e}")
            import traceback
            traceback.print_exc()
            return JSONResponse(
                status_code=500,
                content={"error": "server_error", "message": str(e)},
            )

    # Store assistant response in PostgreSQL if chat exists
    assistant_metadata = {"tools_called": tools_called} if tools_called else None
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
