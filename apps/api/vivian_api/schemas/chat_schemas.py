"""Chat Pydantic schemas."""

from datetime import datetime
from typing import List, Optional, Any, Dict
from pydantic import BaseModel


class ChatCreate(BaseModel):
    """Schema for creating a chat."""
    title: str = "New Chat"
    model: Optional[str] = None


class ChatResponse(BaseModel):
    """Schema for chat response."""
    id: str
    user_id: str
    title: str
    summary: Optional[str] = None
    model: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatWithMessagesResponse(ChatResponse):
    """Schema for chat with messages."""
    messages: List["ChatMessageResponse"] = []


class ChatMessageCreate(BaseModel):
    """Schema for creating a message."""
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class ChatMessageResponse(BaseModel):
    """Schema for message response."""
    id: str
    chat_id: str
    role: str
    content: str
    timestamp: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class ChatListResponse(BaseModel):
    """Schema for list of chats."""
    chats: List[ChatResponse]
    total: int


class UpdateTitleRequest(BaseModel):
    """Schema for updating chat title."""
    title: str


class GenerateSummaryRequest(BaseModel):
    """Schema for generating summary."""
    pass


class GenerateSummaryResponse(BaseModel):
    """Schema for summary generation response."""
    summary: str
    title: str
