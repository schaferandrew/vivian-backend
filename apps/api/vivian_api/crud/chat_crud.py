"""Compatibility CRUD facade that delegates to typed repositories."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from vivian_api.models.chat_models import Chat, ChatMessage
from vivian_api.repositories import (
    DEFAULT_USER_ID,
    ChatMessageRepository,
    ChatRepository,
)


def create_chat(
    db: Session,
    user_id: str = DEFAULT_USER_ID,
    title: str = "New Chat",
    model: str | None = None,
) -> Chat:
    """Create a new chat."""
    return ChatRepository(db).create(user_id=user_id, title=title, model=model)


def get_chat(db: Session, chat_id: str) -> Chat | None:
    """Get a chat by ID."""
    return ChatRepository(db).get(chat_id)


def get_chats(
    db: Session,
    user_id: str = DEFAULT_USER_ID,
    limit: int = 50,
    offset: int = 0,
) -> list[Chat]:
    """Get all chats for a user, ordered by updated_at descending."""
    return ChatRepository(db).list_for_user(user_id=user_id, limit=limit, offset=offset)


def update_chat_title(db: Session, chat_id: str, title: str) -> Chat | None:
    """Update chat title."""
    return ChatRepository(db).update_title(chat_id, title)


def update_chat_summary(db: Session, chat_id: str, summary: str) -> Chat | None:
    """Update chat summary."""
    return ChatRepository(db).update_summary(chat_id, summary)


def delete_chat(db: Session, chat_id: str) -> bool:
    """Delete a chat and all its messages."""
    return ChatRepository(db).delete(chat_id)


def create_message(
    db: Session,
    chat_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> ChatMessage:
    """Create a new message in a chat."""
    return ChatMessageRepository(db).create(
        chat_id=chat_id,
        role=role,
        content=content,
        metadata=metadata,
    )


def get_messages(db: Session, chat_id: str) -> list[ChatMessage]:
    """Get all messages for a chat."""
    return ChatMessageRepository(db).list_for_chat(chat_id)


def delete_message(db: Session, message_id: str) -> bool:
    """Delete a message."""
    return ChatMessageRepository(db).delete(message_id)


def get_recent_chats(db: Session, user_id: str = DEFAULT_USER_ID, limit: int = 10) -> list[Chat]:
    """Get most recent chats."""
    return ChatRepository(db).list_for_user(user_id=user_id, limit=limit)
