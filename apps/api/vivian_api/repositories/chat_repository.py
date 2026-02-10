"""Typed repositories for chat and message persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.encoders import jsonable_encoder

from vivian_api.models.chat_models import Chat, ChatMessage


DEFAULT_USER_ID = "default_user"


class ChatRepository:
    """Repository for ``Chat`` entities."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        title: str = "New Chat",
        model: str | None = None,
    ) -> Chat:
        chat = Chat(
            id=str(uuid.uuid4()),
            user_id=user_id,
            title=title,
            model=model,
        )
        self.db.add(chat)
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def get(self, chat_id: str) -> Chat | None:
        return self.db.scalar(select(Chat).where(Chat.id == chat_id))

    def list_for_user(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chat]:
        stmt = (
            select(Chat)
            .where(Chat.user_id == user_id)
            .order_by(Chat.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def update_title(self, chat_id: str, title: str) -> Chat | None:
        chat = self.get(chat_id)
        if not chat:
            return None
        chat.title = title
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def update_summary(self, chat_id: str, summary: str) -> Chat | None:
        chat = self.get(chat_id)
        if not chat:
            return None
        chat.summary = summary
        self.db.commit()
        self.db.refresh(chat)
        return chat

    def touch(self, chat: Chat) -> None:
        chat.updated_at = datetime.utcnow()

    def delete(self, chat_id: str) -> bool:
        chat = self.get(chat_id)
        if not chat:
            return False
        self.db.delete(chat)
        self.db.commit()
        return True


class ChatMessageRepository:
    """Repository for ``ChatMessage`` entities."""

    def __init__(self, db: Session):
        self.db = db
        self.chat_repo = ChatRepository(db)

    def create(
        self,
        *,
        chat_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessage:
        chat = self.chat_repo.get(chat_id)
        if not chat:
            raise ValueError(f"Chat {chat_id} not found")

        message = ChatMessage(
            id=str(uuid.uuid4()),
            chat_id=chat_id,
            role=role,
            content=content,
            extra_data=jsonable_encoder(metadata) if metadata is not None else None,
        )
        self.db.add(message)
        self.chat_repo.touch(chat)
        self.db.commit()
        self.db.refresh(message)
        return message

    def list_for_chat(self, chat_id: str) -> list[ChatMessage]:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.chat_id == chat_id)
            .order_by(ChatMessage.timestamp.asc())
        )
        return list(self.db.scalars(stmt).all())

    def delete(self, message_id: str) -> bool:
        message = self.db.scalar(select(ChatMessage).where(ChatMessage.id == message_id))
        if not message:
            return False
        self.db.delete(message)
        self.db.commit()
        return True
