"""Chat session management with full conversation history."""

import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from fastapi import WebSocket

from vivian_shared.models import ParsedReceipt, ExpenseSchema


class FlowType(str, Enum):
    """Types of multi-turn flows."""
    UPLOAD = "upload"
    BULK_IMPORT = "bulk_import"
    BALANCE = "balance"
    NONE = "none"


class FlowStatus(str, Enum):
    """Flow execution status."""
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class UploadedFileRef(BaseModel):
    """Reference to an uploaded file."""
    file_id: str
    filename: str
    temp_path: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class FlowStepRecord(BaseModel):
    """Record of a flow step."""
    step_name: str
    status: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message: Optional[str] = None


class FlowData(BaseModel):
    """Data for active flow."""
    flow_type: FlowType
    
    # Upload flow data
    upload_temp_path: Optional[str] = None
    parsed_receipt: Optional[ParsedReceipt] = None
    confirmation_received: bool = False
    edited_data: Optional[ExpenseSchema] = None
    selected_status: Optional[str] = None
    
    # Bulk import flow data
    import_method: Optional[str] = None  # "desktop" or "browser"
    directory_path: Optional[str] = None
    uploaded_files: List[str] = Field(default_factory=list)
    current_file_index: int = 0
    skip_errors: bool = True
    
    # Balance flow data
    balance_result: Optional[Dict[str, Any]] = None


class FlowState(BaseModel):
    """Current flow state."""
    flow_id: str = Field(default_factory=lambda: f"flow_{uuid.uuid4().hex[:8]}")
    flow_type: FlowType
    status: FlowStatus = FlowStatus.ACTIVE
    current_step: str = "started"
    step_history: List[FlowStepRecord] = Field(default_factory=list)
    data: FlowData
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    timeout_at: Optional[datetime] = None


class SessionContext(BaseModel):
    """Session-level context."""
    uploaded_files: List[UploadedFileRef] = Field(default_factory=list)
    preferences: Dict[str, Any] = Field(default_factory=dict)
    preferred_import_method: Optional[str] = None
    last_balance_query: Optional[datetime] = None
    last_balance_result: Optional[Dict[str, Any]] = None
    web_search_enabled: bool = False  # Web search costs ~$0.02/query, default OFF


class ErrorRecoveryState(BaseModel):
    """State for error recovery."""
    error_category: str
    error_message: str
    flow_id: str
    step: str
    retry_count: int = 0
    selected_recovery_option: Optional[str] = None


class ChatSession(BaseModel):
    """Complete chat session state."""
    
    # Identity
    session_id: str = Field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    
    # Connection (excluded from serialization)
    websocket: Optional[Any] = Field(None, exclude=True)
    
    # Conversation history (full history preserved)
    messages: List[Dict[str, Any]] = Field(default_factory=list)
    max_history: int = 100
    
    # Flow state machine
    current_flow: Optional[FlowState] = None
    flow_history: List[FlowState] = Field(default_factory=list)
    
    # Context
    context: SessionContext = Field(default_factory=SessionContext)
    
    # Error recovery
    pending_recovery: Optional[ErrorRecoveryState] = None
    
    class Config:
        arbitrary_types_allowed = True
    
    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None):
        """Add a message to conversation history."""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            "metadata": metadata or {}
        }
        self.messages.append(message)
        
        # Trim history if exceeds max
        if len(self.messages) > self.max_history:
            self.messages = self.messages[-self.max_history:]
        
        self.last_activity_at = datetime.utcnow()
    
    def get_conversation_history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get conversation history as list of messages."""
        if limit:
            return self.messages[-limit:]
        return self.messages
    
    def start_flow(self, flow_type: FlowType, initial_data: Optional[Dict] = None):
        """Start a new flow."""
        # Save current flow to history if exists
        if self.current_flow:
            self.flow_history.append(self.current_flow)
        
        # Create new flow
        data = FlowData(flow_type=flow_type, **(initial_data or {}))
        self.current_flow = FlowState(
            flow_type=flow_type,
            data=data,
            timeout_at=datetime.utcnow() + timedelta(minutes=30)
        )
        self.last_activity_at = datetime.utcnow()
    
    def end_flow(self):
        """End current flow."""
        if self.current_flow:
            self.current_flow.status = FlowStatus.COMPLETED
            self.flow_history.append(self.current_flow)
            self.current_flow = None
        self.last_activity_at = datetime.utcnow()
    
    def update_flow_step(self, step_name: str, status: str, message: Optional[str] = None):
        """Update current flow step."""
        if self.current_flow:
            self.current_flow.current_step = step_name
            self.current_flow.step_history.append(
                FlowStepRecord(
                    step_name=step_name,
                    status=status,
                    message=message
                )
            )
            self.current_flow.updated_at = datetime.utcnow()
            self.last_activity_at = datetime.utcnow()
    
    def is_expired(self, timeout_minutes: int = 30) -> bool:
        """Check if session has expired due to inactivity."""
        expiration = self.last_activity_at + timedelta(minutes=timeout_minutes)
        return datetime.utcnow() > expiration
    
    def wipe(self):
        """Wipe session clean while keeping session_id."""
        self.messages = []
        self.current_flow = None
        self.flow_history = []
        self.context = SessionContext()
        self.pending_recovery = None
        self.last_activity_at = datetime.utcnow()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (excluding websocket)."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "last_activity_at": self.last_activity_at.isoformat(),
            "messages": self.messages,
            "current_flow": self.current_flow.model_dump() if self.current_flow else None,
            "context": self.context.model_dump()
        }


class SessionManager:
    """Manages chat sessions."""
    
    def __init__(self):
        self._sessions: Dict[str, ChatSession] = {}
        self._websocket_map: Dict[WebSocket, str] = {}
    
    def create_session(self) -> ChatSession:
        """Create a new chat session."""
        session = ChatSession()
        self._sessions[session.session_id] = session
        return session
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get session by ID."""
        return self._sessions.get(session_id)
    
    def get_session_by_websocket(self, websocket: WebSocket) -> Optional[ChatSession]:
        """Get session associated with a WebSocket."""
        session_id = self._websocket_map.get(websocket)
        if session_id:
            return self._sessions.get(session_id)
        return None
    
    def associate_websocket(self, session_id: str, websocket: WebSocket):
        """Associate a WebSocket with a session."""
        self._websocket_map[websocket] = session_id
        session = self._sessions.get(session_id)
        if session:
            session.websocket = websocket
    
    def disassociate_websocket(self, websocket: WebSocket):
        """Remove WebSocket association."""
        if websocket in self._websocket_map:
            session_id = self._websocket_map.pop(websocket)
            session = self._sessions.get(session_id)
            if session:
                session.websocket = None
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            # Find and remove WebSocket association
            for ws, sid in list(self._websocket_map.items()):
                if sid == session_id:
                    del self._websocket_map[ws]
                    break
            del self._sessions[session_id]
            return True
        return False
    
    def cleanup_expired_sessions(self, timeout_minutes: int = 30):
        """Remove expired sessions."""
        expired = [
            sid for sid, session in self._sessions.items()
            if session.is_expired(timeout_minutes)
        ]
        for sid in expired:
            self.delete_session(sid)
        return len(expired)
    
    def list_active_sessions(self) -> List[str]:
        """List all active session IDs."""
        return list(self._sessions.keys())


# Global session manager instance
session_manager = SessionManager()
