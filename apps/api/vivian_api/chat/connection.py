"""WebSocket connection manager for chat."""

import json
from typing import Any, Dict, Optional
from fastapi import WebSocket, WebSocketDisconnect

from vivian_api.chat.message_protocol import (
    ChatMessage, MessageType, AgentTextPayload, ConfirmationRequestPayload,
    FlowEventPayload, StatusPayload, ErrorPayload, TypingPayload,
    HandshakeResponsePayload, ActionButton, ErrorRecoveryOption
)
from vivian_api.chat.session import ChatSession, session_manager


class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self):
        self.active_connections: Dict[WebSocket, str] = {}  # websocket -> session_id
    
    async def connect(self, websocket: WebSocket, session_id: Optional[str] = None):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        
        # Get or create session
        if session_id and session_manager.get_session(session_id):
            session = session_manager.get_session(session_id)
        else:
            session = session_manager.create_session()
        
        # Associate WebSocket with session
        session_manager.associate_websocket(session.session_id, websocket)
        self.active_connections[websocket] = session.session_id
        
        return session
    
    def disconnect(self, websocket: WebSocket):
        """Handle WebSocket disconnection."""
        if websocket in self.active_connections:
            session_manager.disassociate_websocket(websocket)
            del self.active_connections[websocket]
    
    async def send_message(self, websocket: WebSocket, message: ChatMessage):
        """Send a message to a specific WebSocket."""
        await websocket.send_json(message.model_dump(mode='json'))
    
    async def send_to_session(self, session_id: str, message: ChatMessage):
        """Send message to all WebSockets associated with a session."""
        session = session_manager.get_session(session_id)
        if session and session.websocket:
            await self.send_message(session.websocket, message)
    
    async def send_text(
        self, 
        session: ChatSession, 
        content: str,
        add_to_history: bool = True
    ):
        """Send text message from agent."""
        if add_to_history:
            session.add_message("assistant", content)
        
        message = ChatMessage(
            type=MessageType.AGENT_TEXT,
            session_id=session.session_id,
            payload=AgentTextPayload(content=content).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message)
    
    async def send_confirmation(
        self,
        session: ChatSession,
        prompt_id: str,
        message: str,
        actions: list,
        display_data: Optional[Dict] = None,
        timeout_seconds: int = 300
    ):
        """Send confirmation request with action buttons."""
        action_buttons = [
            ActionButton(**action) if isinstance(action, dict) else action
            for action in actions
        ]
        
        message_obj = ChatMessage(
            type=MessageType.CONFIRMATION_REQUEST,
            session_id=session.session_id,
            payload=ConfirmationRequestPayload(
                prompt_id=prompt_id,
                message=message,
                actions=action_buttons,
                display_data=display_data,
                timeout_seconds=timeout_seconds
            ).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message_obj)
    
    async def send_flow_event(
        self,
        session: ChatSession,
        event: str,
        step_name: Optional[str] = None,
        step_status: Optional[str] = None,
        progress_percent: Optional[int] = None,
        message: Optional[str] = None,
        metadata: Optional[Dict] = None
    ):
        """Send flow state change event."""
        from vivian_api.chat.message_protocol import FlowStep
        
        step = None
        if step_name:
            step = FlowStep(
                name=step_name,
                status=step_status or "in_progress",
                progress_percent=progress_percent,
                message=message
            )
        
        flow_id = session.current_flow.flow_id if session.current_flow else "none"
        flow_type = session.current_flow.flow_type.value if session.current_flow else "none"
        
        message_obj = ChatMessage(
            type=MessageType.FLOW_EVENT,
            session_id=session.session_id,
            payload=FlowEventPayload(
                flow_id=flow_id,
                flow_type=flow_type,
                event=event,
                step=step,
                metadata=metadata or {}
            ).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message_obj)
    
    async def send_status(
        self,
        session: ChatSession,
        category: str,
        message: str,
        progress: Optional[Dict] = None,
        details: Optional[Dict] = None
    ):
        """Send status/progress update."""
        message_obj = ChatMessage(
            type=MessageType.STATUS,
            session_id=session.session_id,
            payload=StatusPayload(
                category=category,
                message=message,
                progress=progress,
                details=details
            ).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message_obj)
    
    async def send_error(
        self,
        session: ChatSession,
        error_id: str,
        category: str,
        severity: str,
        message: str,
        details: Optional[Dict] = None,
        recovery_options: Optional[list] = None,
        retry_count: int = 0
    ):
        """Send error message with recovery options."""
        options = []
        if recovery_options:
            options = [
                ErrorRecoveryOption(**opt) if isinstance(opt, dict) else opt
                for opt in recovery_options
            ]
        
        message_obj = ChatMessage(
            type=MessageType.ERROR,
            session_id=session.session_id,
            payload=ErrorPayload(
                error_id=error_id,
                category=category,
                severity=severity,
                message=message,
                details=details,
                recovery_options=options,
                retry_count=retry_count
            ).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message_obj)
    
    async def send_typing(self, session: ChatSession, is_typing: bool = True):
        """Send typing indicator."""
        message_obj = ChatMessage(
            type=MessageType.TYPING,
            session_id=session.session_id,
            payload=TypingPayload(is_typing=is_typing).model_dump(mode='json')
        )
        await self.send_to_session(session.session_id, message_obj)
    
    async def broadcast(self, message: str):
        """Broadcast message to all connected clients."""
        for websocket in self.active_connections:
            await websocket.send_text(message)


# Global connection manager instance
connection_manager = ConnectionManager()
