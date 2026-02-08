"""Chat WebSocket router."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import httpx

from vivian_api.chat.connection import connection_manager
from vivian_api.chat.session import session_manager
from vivian_api.chat.handler import chat_handler
from vivian_api.chat.message_protocol import ChatMessage
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.llm import get_chat_completion, OpenRouterCreditsError, OpenRouterRateLimitError
from vivian_api.config import AVAILABLE_MODELS, DEFAULT_MODEL, Settings, check_ollama_status, get_selected_model, set_selected_model
from vivian_api.db.database import get_db
from vivian_api.repositories import ChatMessageRepository, ChatRepository


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    chat_id: str | None = None
    web_search_enabled: bool = False


class ChatResponse(BaseModel):
    response: str
    session_id: str
    chat_id: str


class ModelSelectRequest(BaseModel):
    model_id: str


settings = Settings()

SUMMARY_MODEL_ID = "meta-llama/llama-3.3-70b-instruct:free"


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

    if not first_user_message:
        return "New Chat", "New Chat"

    content_preview = first_user_message[:100].replace("\n", " ")
    summary_source = latest_user_message or first_user_message

    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "can", "do", "for", "from",
        "get", "give", "hello", "help", "hi", "how", "i", "in", "installed", "is",
        "it", "just", "let", "me", "my", "of", "on", "or", "our", "please", "set",
        "show", "test", "that", "the", "this", "to", "up", "we", "with", "you",
        "your",
    }

    keyword_priority = [
        "markdown", "renderer", "test", "summary", "title", "chat", "session",
        "hsa", "receipt", "balance", "upload", "settings", "model",
    ]

    def normalize_three_word_title(raw: str, fallback: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9\s'-]", " ", raw).strip()
        parts = [part for part in cleaned.split() if part]
        if not parts:
            fallback_clean = re.sub(r"[^A-Za-z0-9\s'-]", " ", fallback).strip()
            parts = [part for part in fallback_clean.split() if part]
        if not parts:
            return "New Chat"
        short_title = " ".join(parts[:3]).strip()
        return short_title[:40] if short_title else "New Chat"

    def keyword_fallback_title(primary: str, secondary: str = "") -> str:
        source = f"{primary} {secondary}".strip()
        tokens = re.findall(r"[A-Za-z0-9']+", source.lower())

        selected: list[str] = []
        for keyword in keyword_priority:
            if keyword in tokens and keyword not in selected:
                selected.append(keyword)
            if len(selected) >= 3:
                break

        if len(selected) < 3:
            for token in tokens:
                if token in stop_words or len(token) < 3 or token in selected:
                    continue
                selected.append(token)
                if len(selected) >= 3:
                    break

        if not selected:
            return "New Chat"

        words = selected[:3]
        if len(words) == 1:
            words.extend(["Chat", "Summary"])
        elif len(words) == 2:
            words.append("Chat")

        return " ".join(word.capitalize() for word in words)

    system_prompt = """You write chat list titles.

Rules:
- Return EXACTLY 3 words for TITLE.
- TITLE must be specific to the user intent, not generic.
- Use plain words only (no punctuation, no quotes, no emoji).
- Prefer noun-heavy phrasing (what user wants), not conversational phrasing.
- SUMMARY must be identical to TITLE.
- Ignore conversational lead-ins such as "I just installed", "Hello", or "Can you".

Example:
User asks about testing markdown rendering.
TITLE: Markdown Renderer Test
SUMMARY: Markdown Renderer Test

Output format (must match exactly):
TITLE: <three words>
SUMMARY: <same three words>"""

    context_block = "\n".join(
        f"- {msg[:220].replace(chr(10), ' ')}" for msg in user_messages[-4:]
    )
    user_prompt = (
        f"Conversation user messages:\n{context_block}\n\n"
        f"First user message: {first_user_message}\n"
        f"Latest user message: {latest_user_message}\n\n"
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

                generated_text = title_raw or summary_raw or content_preview
                short_title = normalize_three_word_title(generated_text, summary_source)

                weak_prefixes = ("i ", "i just", "hello", "hi ")
                if (
                    short_title.lower() == "new chat"
                    or short_title.lower().startswith(weak_prefixes)
                ):
                    short_title = keyword_fallback_title(summary_source, first_user_message)

                short_summary = short_title

                return short_title, short_summary
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

    # Store user message in PostgreSQL if chat exists
    if db_chat:
        message_repo.create(chat_id=db_chat.id, role="user", content=request.message)

    # Store user message in session (in-memory)
    session.add_message(role="user", content=request.message)

    # Convert session messages to OpenRouter format; prepend system prompt so model stays in character
    messages = [
        {"role": "system", "content": VivianPersonality.get_system_prompt()},
        *(
            {"role": msg["role"], "content": msg["content"]}
            for msg in session.messages
        ),
    ]

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
    if db_chat:
        message_repo.create(chat_id=db_chat.id, role="assistant", content=response_text)

        # Generate summary on server side after saving messages
        try:
            db_messages = message_repo.list_for_chat(db_chat.id)
            messages_dict = [msg.to_dict() for msg in db_messages]
            title, summary = await generate_summary_from_messages(messages_dict)

            if title:
                chat_repo.update_title(db_chat.id, title)
            if summary:
                chat_repo.update_summary(db_chat.id, summary)
        except Exception as e:
            print(f"Error generating summary: {e}")

    # Store assistant response in session (in-memory)
    session.add_message(role="assistant", content=response_text)

    return ChatResponse(
        response=response_text,
        session_id=session.session_id,
        chat_id=db_chat.id,
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
