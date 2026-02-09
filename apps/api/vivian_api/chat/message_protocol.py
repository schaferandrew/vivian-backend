"""WebSocket message protocol models for chat system."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Message types for WebSocket protocol."""
    # Client to Server
    TEXT = "text"
    COMMAND = "command"
    FILE_UPLOAD = "file_upload"
    FILE_CHUNK = "file_chunk"
    ACTION = "action"
    HANDSHAKE = "handshake"
    SETTINGS = "settings"  # For updating session settings like web_search_enabled

    # Server to Client
    AGENT_TEXT = "agent_text"
    CONFIRMATION_REQUEST = "confirmation_request"
    FLOW_EVENT = "flow_event"
    STATUS = "status"
    ERROR = "error"
    TYPING = "typing"
    HANDSHAKE_RESPONSE = "handshake_response"
    SETTINGS_RESPONSE = "settings_response"  # Confirmation of settings update


class ActionButton(BaseModel):
    """Action button for human-in-the-loop interactions."""
    id: str
    label: str
    style: Literal["primary", "secondary", "danger"] = "secondary"
    icon: Optional[str] = None
    description: Optional[str] = None


class ChatMessage(BaseModel):
    """Base WebSocket message structure."""
    message_id: str = Field(default_factory=lambda: f"msg_{datetime.utcnow().timestamp()}")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: MessageType
    session_id: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


# Client to Server Messages

class TextPayload(BaseModel):
    """Payload for text messages from client."""
    content: str
    reply_to: Optional[str] = None


class CommandPayload(BaseModel):
    """Payload for slash commands."""
    command: str
    args: List[str] = Field(default_factory=list)
    raw: str


class FileUploadPayload(BaseModel):
    """Payload for file upload initiation."""
    filename: str
    mime_type: str
    size_bytes: int
    file_id: str
    is_chunked: bool = False
    total_chunks: int = 1
    chunk_index: int = 0


class FileChunkPayload(BaseModel):
    """Payload for file chunk transfer."""
    file_id: str
    chunk_index: int
    total_chunks: int
    data: str  # base64 encoded
    is_final: bool


class ActionPayload(BaseModel):
    """Payload for action/button responses."""
    action_id: str
    action_type: Literal["confirm", "reject", "edit", "retry", "cancel", "custom"]
    context: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)


class HandshakePayload(BaseModel):
    """Payload for initial connection handshake."""
    client_version: str = "1.0.0"
    requested_capabilities: List[str] = Field(default_factory=list)
    resume_session_id: Optional[str] = None


class SettingsPayload(BaseModel):
    """Payload for updating session settings."""
    setting: Literal["web_search_enabled", "enabled_mcp_servers"]
    value: bool | list[str]


class SettingsResponsePayload(BaseModel):
    """Payload for settings update confirmation."""
    setting: str
    value: bool | list[str]
    success: bool
    message: Optional[str] = None


# Server to Client Messages

class AgentTextPayload(BaseModel):
    """Payload for agent text responses."""
    content: str
    format: Literal["plain", "markdown"] = "markdown"
    persona: str = "vivian"


class ConfirmationRequestPayload(BaseModel):
    """Payload for human-in-the-loop confirmation requests."""
    prompt_id: str
    message: str
    display_data: Optional[Dict[str, Any]] = None
    actions: List[ActionButton]
    timeout_seconds: int = 300


class FlowStep(BaseModel):
    """Step information for flow events."""
    name: str
    status: Literal["started", "in_progress", "completed", "error"]
    progress_percent: Optional[int] = None
    message: Optional[str] = None


class FlowEventPayload(BaseModel):
    """Payload for flow state change events."""
    flow_id: str
    flow_type: str
    event: Literal["started", "step_changed", "completed", "error", "paused"]
    step: Optional[FlowStep] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StatusPayload(BaseModel):
    """Payload for progress/status updates."""
    category: Literal["upload_progress", "parse_progress", "save_progress", "general"]
    message: str
    progress: Optional[Dict[str, Any]] = None
    details: Optional[Dict[str, Any]] = None


class ErrorRecoveryOption(BaseModel):
    """Recovery option for error messages."""
    id: str
    label: str
    description: Optional[str] = None


class ErrorPayload(BaseModel):
    """Payload for error messages with recovery options."""
    error_id: str
    category: Literal["parse_error", "upload_error", "mcp_error", "flow_error", "system_error"]
    severity: Literal["recoverable", "user_fixable", "external", "fatal"]
    message: str
    details: Optional[Dict[str, Any]] = None
    recovery_options: List[ErrorRecoveryOption] = Field(default_factory=list)
    retry_count: int = 0


class TypingPayload(BaseModel):
    """Payload for typing indicators."""
    is_typing: bool
    estimated_duration_ms: Optional[int] = None


class HandshakeResponsePayload(BaseModel):
    """Payload for handshake response."""
    session_id: str
    server_version: str = "0.1.0"
    granted_capabilities: List[str] = Field(default_factory=list)
    welcome_message: str
