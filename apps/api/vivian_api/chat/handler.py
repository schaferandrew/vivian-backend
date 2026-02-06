"""Main chat message handler and router."""

import os
import shutil
import uuid
from pathlib import Path
from typing import Optional

from vivian_api.chat.session import ChatSession, session_manager, FlowType
from vivian_api.chat.connection import connection_manager
from vivian_api.chat.intent_router import IntentRouter, IntentCategory
from vivian_api.chat.personality import VivianPersonality
from vivian_api.chat.flows.upload import ReceiptUploadFlow
from vivian_api.chat.flows.bulk_import import BulkImportFlow
from vivian_api.chat.flows.balance import BalanceFlow
from vivian_api.chat.message_protocol import (
    ChatMessage, MessageType, TextPayload, CommandPayload,
    ActionPayload, FileUploadPayload
)
from vivian_api.config import Settings

settings = Settings()


class ChatHandler:
    """Main chat message handler."""
    
    def __init__(self):
        self.intent_router = IntentRouter()
        self.upload_flow = ReceiptUploadFlow()
        self.bulk_import_flow = BulkImportFlow()
        self.balance_flow = BalanceFlow()
    
    async def handle_message(self, session: ChatSession, message: ChatMessage):
        """Route and handle incoming chat messages."""
        
        if message.type == MessageType.TEXT:
            await self._handle_text(session, message.payload.get("content", ""))
            
        elif message.type == MessageType.COMMAND:
            await self._handle_command(session, message.payload)
            
        elif message.type == MessageType.ACTION:
            await self._handle_action(session, message.payload)
            
        elif message.type == MessageType.FILE_UPLOAD:
            await self._handle_file_upload(session, message.payload)
            
        elif message.type == MessageType.HANDSHAKE:
            await self._handle_handshake(session)
    
    async def _handle_text(self, session: ChatSession, content: str):
        """Handle text messages."""
        # Add to history
        session.add_message("user", content)
        
        # Check if we're in an active flow
        if session.current_flow:
            await self._handle_flow_input(session, content)
            return
        
        # Classify intent
        classification = await self.intent_router.classify(
            content,
            session.get_conversation_history(limit=5)
        )
        
        # Route based on intent
        if classification.intent == IntentCategory.RECEIPT_UPLOAD:
            await self.upload_flow.start(session)
            
        elif classification.intent == IntentCategory.BULK_IMPORT:
            # Check if path provided
            directory_path = self.intent_router.extract_directory_path(content)
            if directory_path:
                await self.bulk_import_flow.start(session)
                await self.bulk_import_flow.handle_method_selection(session, "desktop")
                await self.bulk_import_flow.handle_desktop_path(session, directory_path)
            else:
                await self.bulk_import_flow.start(session)
                
        elif classification.intent == IntentCategory.BALANCE_QUERY:
            await self.balance_flow.execute(session)
            
        elif classification.intent == IntentCategory.GREETING:
            await connection_manager.send_text(
                session,
                VivianPersonality.WELCOME_RETURNING if len(session.messages) > 2 else VivianPersonality.WELCOME_NEW
            )
            
        elif classification.intent == IntentCategory.HELP:
            await connection_manager.send_text(session, VivianPersonality.COMMAND_HELP)
            
        elif classification.intent == IntentCategory.GOODBYE:
            await connection_manager.send_text(
                session,
                "Goodbye! Feel free to come back anytime you need help with your receipts. 👋"
            )
            
        elif classification.intent == IntentCategory.UNCLEAR:
            if classification.clarification_prompt:
                await connection_manager.send_text(session, classification.clarification_prompt)
            else:
                await connection_manager.send_text(
                    session,
                    "I'm not quite sure what you'd like to do. You can:\n\n"
                    "• Upload a receipt (say \"upload receipt\" or use /upload)\n"
                    "• Check your HSA balance (say \"what's my balance?\" or use /balance)\n"
                    "• Import multiple receipts (say \"import receipts\" or use /import)\n\n"
                    "What would you like to try?"
                )
        else:
            # General chat response
            await connection_manager.send_text(
                session,
                "I can help you manage your HSA receipts and check your balance. "
                "What would you like to do?"
            )
    
    async def _handle_command(self, session: ChatSession, payload: dict):
        """Handle slash commands."""
        command = payload.get("command", "").lower()
        args = payload.get("args", [])
        
        if command == "/upload":
            await self.upload_flow.start(session)
            
        elif command == "/import":
            if args:
                # Direct path provided
                await self.bulk_import_flow.start(session)
                await self.bulk_import_flow.handle_method_selection(session, "desktop")
                await self.bulk_import_flow.handle_desktop_path(session, args[0])
            else:
                await self.bulk_import_flow.start(session)
                
        elif command == "/balance":
            await self.balance_flow.execute(session)
            
        elif command == "/new":
            session.wipe()
            await connection_manager.send_text(
                session,
                "Started a fresh conversation! " + VivianPersonality.WELCOME_NEW
            )
            
        elif command == "/help":
            await connection_manager.send_text(session, VivianPersonality.COMMAND_HELP)
            
        else:
            await connection_manager.send_text(
                session,
                f"Unknown command: /{command}. Type /help for available commands."
            )
    
    async def _handle_action(self, session: ChatSession, payload: dict):
        """Handle action/button responses."""
        action_type = payload.get("action_type", "")
        action_data = payload.get("data", {})
        context = payload.get("context", {})
        
        # Route to active flow
        if session.current_flow:
            flow_type = session.current_flow.flow_type
            
            if flow_type == FlowType.UPLOAD:
                await self.upload_flow.handle_action(session, action_type, action_data)
                
            elif flow_type == FlowType.BULK_IMPORT:
                await self._handle_bulk_import_action(session, action_type, action_data)
        else:
            # Handle standalone actions
            if action_type in ["upload_receipt", "view_details"]:
                await self.upload_flow.start(session)
            elif action_type == "no_thanks":
                await connection_manager.send_text(
                    session,
                    "No problem! Let me know if you need anything else."
                )
    
    async def _handle_bulk_import_action(self, session: ChatSession, action_type: str, action_data: dict):
        """Handle bulk import specific actions."""
        if action_type in ["desktop", "browser"]:
            await self.bulk_import_flow.handle_method_selection(session, action_type)
            
        elif action_type in ["all_unreimbursed", "all_reimbursed", "ask_each"]:
            status_map = {
                "all_unreimbursed": "unreimbursed",
                "all_reimbursed": "reimbursed",
                "ask_each": "ask_each"
            }
            await self.bulk_import_flow.process_files(session, status_map.get(action_type, "unreimbursed"))
            
        elif action_type == "retry_path":
            await connection_manager.send_text(
                session,
                "Please provide the correct folder path:"
            )
            
        elif action_type == "switch_browser":
            await self.bulk_import_flow.handle_method_selection(session, "browser")
    
    async def _handle_file_upload(self, session: ChatSession, payload: dict):
        """Handle file upload initiation."""
        filename = payload.get("filename", "")
        file_id = payload.get("file_id", "")
        
        # Store reference - actual file data comes via HTTP endpoint
        await connection_manager.send_text(
            session,
            f"Received file: {filename}. Processing..."
        )
    
    async def _handle_flow_input(self, session: ChatSession, content: str):
        """Handle input when in an active flow."""
        flow = session.current_flow
        
        if flow.flow_type == FlowType.BULK_IMPORT:
            if flow.data.import_method == "desktop" and not flow.data.directory_path:
                # User provided path
                await self.bulk_import_flow.handle_desktop_path(session, content)
            else:
                await connection_manager.send_text(
                    session,
                    "I'm still working on the previous request. Please wait or type /new to start over."
                )
        else:
            await connection_manager.send_text(
                session,
                "I'm still processing your receipt. Please wait or type /new to cancel."
            )
    
    async def _handle_handshake(self, session: ChatSession):
        """Handle initial connection handshake."""
        from vivian_api.chat.message_protocol import HandshakeResponsePayload
        
        welcome = VivianPersonality.WELCOME_NEW
        
        message = ChatMessage(
            type=MessageType.HANDSHAKE_RESPONSE,
            session_id=session.session_id,
            payload=HandshakeResponsePayload(
                session_id=session.session_id,
                granted_capabilities=["file_upload", "actions", "typing_indicator"],
                welcome_message=welcome
            ).model_dump()
        )
        
        await connection_manager.send_to_session(session.session_id, message)


# Global handler instance
chat_handler = ChatHandler()
