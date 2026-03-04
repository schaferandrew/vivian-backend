"""Chat history API router with PostgreSQL storage."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from vivian_api.auth.dependencies import CurrentUserContext, get_current_user_context
from vivian_api.db.database import get_db
from vivian_api.repositories import ChatMessageRepository, ChatRepository
from vivian_api.schemas import chat_schemas


router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("/", response_model=chat_schemas.ChatListResponse)
async def list_chats(
    limit: int = 50,
    offset: int = 0,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """List all chats for the user."""
    chats = ChatRepository(db).list_for_user(
        user_id=current_user.user.id,
        limit=limit,
        offset=offset,
    )
    total = len(chats)
    return {"chats": chats, "total": total}


@router.post("/", response_model=chat_schemas.ChatResponse, status_code=status.HTTP_201_CREATED)
async def create_chat(
    chat: chat_schemas.ChatCreate = None,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Create a new chat."""
    if chat is None:
        chat = chat_schemas.ChatCreate()
    db_chat = ChatRepository(db).create(
        user_id=current_user.user.id,
        title=chat.title,
        model=chat.model,
    )
    return db_chat


@router.get("/{chat_id}")
async def get_chat(
    chat_id: str,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Get a chat with all its messages."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)
    db_chat = chat_repo.get(chat_id)
    if not db_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if db_chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = message_repo.list_for_chat(chat_id)

    return {
        "id": db_chat.id,
        "user_id": db_chat.user_id,
        "title": db_chat.title,
        "summary": db_chat.summary,
        "model": db_chat.model,
        "created_at": db_chat.created_at.isoformat() if db_chat.created_at else None,
        "updated_at": db_chat.updated_at.isoformat() if db_chat.updated_at else None,
        "messages": [
            {
                "id": msg.id,
                "chat_id": msg.chat_id,
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None,
                "metadata": msg.extra_data if msg.extra_data else None,
            }
            for msg in messages
        ]
    }


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: str,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Delete a chat."""
    chat_repo = ChatRepository(db)
    chat = chat_repo.get(chat_id)
    if not chat or chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")
    if not chat_repo.delete(chat_id):
        raise HTTPException(status_code=404, detail="Chat not found")


@router.patch("/{chat_id}/title", response_model=chat_schemas.ChatResponse)
async def update_chat_title(
    chat_id: str,
    request: chat_schemas.UpdateTitleRequest,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Update chat title."""
    chat = ChatRepository(db).get(chat_id)
    if not chat or chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")
    db_chat = ChatRepository(db).update_title(chat_id, request.title)
    if not db_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    return db_chat


@router.post("/{chat_id}/messages", response_model=chat_schemas.ChatMessageResponse, status_code=status.HTTP_201_CREATED)
async def add_message(
    chat_id: str,
    message: chat_schemas.ChatMessageCreate,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Add a message to a chat."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)
    db_chat = chat_repo.get(chat_id)
    if not db_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if db_chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")

    db_message = message_repo.create(
        chat_id=chat_id,
        role=message.role,
        content=message.content,
        metadata=message.metadata,
    )
    return db_message


@router.post("/{chat_id}/generate-summary", response_model=chat_schemas.GenerateSummaryResponse)
async def generate_summary(
    chat_id: str,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Generate and save a summary/title for a chat."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)
    db_chat = chat_repo.get(chat_id)
    if not db_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if db_chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = message_repo.list_for_chat(chat_id)
    messages_dict = [msg.to_dict() for msg in messages]

    from vivian_api.chat.router import generate_summary_from_messages
    title, summary = await generate_summary_from_messages(messages_dict)

    if title:
        chat_repo.update_title(chat_id, title)
    if summary:
        chat_repo.update_summary(chat_id, summary)

    return {"summary": summary, "title": title}


@router.get("/{chat_id}/messages", response_model=List[chat_schemas.ChatMessageResponse])
async def get_messages(
    chat_id: str,
    current_user: CurrentUserContext = Depends(get_current_user_context),
    db: Session = Depends(get_db)
):
    """Get all messages for a chat."""
    chat_repo = ChatRepository(db)
    message_repo = ChatMessageRepository(db)
    db_chat = chat_repo.get(chat_id)
    if not db_chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    if db_chat.user_id != current_user.user.id:
        raise HTTPException(status_code=404, detail="Chat not found")

    messages = message_repo.list_for_chat(chat_id)
    return messages
