"""Chat WebSocket router."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vivian_api.chat.connection import connection_manager
from vivian_api.chat.session import session_manager
from vivian_api.chat.handler import chat_handler
from vivian_api.chat.message_protocol import ChatMessage
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.llm import get_chat_completion
from vivian_api.config import AVAILABLE_MODELS, DEFAULT_MODEL, Settings, check_ollama_status


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str


class ModelSelectRequest(BaseModel):
    model_id: str


settings = Settings()


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
            "selectable": True if model["provider"] != "Ollama" else ollama_status.get("available", False)
        }
        models_with_status.append(model_info)
    
    return {
        "models": models_with_status,
        "providers": providers,
        "current_model": settings.selected_model,
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
    
    settings.selected_model = request.model_id
    return {"success": True, "selected_model": settings.selected_model}


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
async def chat_message(request: ChatRequest):
    """HTTP endpoint for chat messages using OpenRouter."""
    # Get or create session
    if request.session_id:
        session = session_manager.get_session(request.session_id)
        if not session:
            session = session_manager.create_session(session_id=request.session_id)
    else:
        session = session_manager.create_session()
    
    # Store user message
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
    response_text = await get_chat_completion(messages)
    
    # Store assistant response
    session.add_message(role="assistant", content=response_text)
    
    return ChatResponse(
        response=response_text,
        session_id=session.session_id
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
