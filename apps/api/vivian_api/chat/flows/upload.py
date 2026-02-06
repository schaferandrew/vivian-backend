"""Receipt upload flow handler."""

import os
import shutil
from pathlib import Path
from typing import Optional

from vivian_api.chat.session import ChatSession, FlowType, FlowStatus
from vivian_api.chat.connection import connection_manager
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.services.mcp_client import MCPClient
from vivian_shared.models import ReimbursementStatus


class ReceiptUploadFlow:
    """Handles single receipt upload flow."""
    
    def __init__(self):
        self.parser = OpenRouterService()
    
    async def start(self, session: ChatSession):
        """Start the upload flow."""
        session.start_flow(FlowType.UPLOAD)
        await connection_manager.send_text(
            session, 
            VivianPersonality.UPLOAD_PROMPT,
            add_to_history=True
        )
    
    async def handle_file_uploaded(self, session: ChatSession, temp_path: str, filename: str):
        """Handle when a file has been uploaded."""
        if not session.current_flow or session.current_flow.flow_type != FlowType.UPLOAD:
            return
        
        # Update flow state
        session.update_flow_step("file_received", "completed")
        session.current_flow.data.upload_temp_path = temp_path
        
        # Start parsing
        await self._parse_receipt(session)
    
    async def _parse_receipt(self, session: ChatSession):
        """Parse the uploaded receipt."""
        session.update_flow_step("parsing", "in_progress", "Parsing receipt...")
        await connection_manager.send_typing(session, True)
        
        temp_path = session.current_flow.data.upload_temp_path
        
        try:
            # Parse with OpenRouter
            result = await self.parser.parse_receipt(temp_path)
            
            if not result.get("success"):
                await self._handle_parse_error(session, result.get("error", "Unknown error"))
                return
            
            parsed_data = result["parsed_data"]
            raw_output = result.get("raw_output", "")
            
            # Calculate confidence
            confidence = 0.9
            if not parsed_data.get("provider"):
                confidence -= 0.2
            if not parsed_data.get("service_date"):
                confidence -= 0.2
            if not parsed_data.get("amount") or parsed_data.get("amount") == 0:
                confidence -= 0.3
            
            from vivian_shared.models import ParsedReceipt, ExpenseSchema
            
            expense = ExpenseSchema(
                provider=parsed_data.get("provider", "Unknown Provider"),
                service_date=parsed_data.get("service_date"),
                paid_date=parsed_data.get("paid_date"),
                amount=float(parsed_data.get("amount", 0)),
                hsa_eligible=parsed_data.get("hsa_eligible", True),
                raw_model_output=raw_output
            )
            
            parsed_receipt = ParsedReceipt(
                expense=expense,
                confidence=max(0, confidence),
                parsing_errors=[] if confidence > 0.7 else ["Low confidence in some fields"]
            )
            
            session.current_flow.data.parsed_receipt = parsed_receipt
            session.update_flow_step("parsing", "completed")
            
            # Check if needs review
            if confidence < 0.85:
                await self._request_review(session, parsed_receipt)
            else:
                await self._request_confirmation(session, parsed_receipt)
                
        except Exception as e:
            await self._handle_parse_error(session, str(e))
    
    async def _request_review(self, session: ChatSession, parsed_receipt):
        """Request user review for low confidence parsing."""
        details = VivianPersonality.format_receipt_details({
            "provider": parsed_receipt.expense.provider,
            "service_date": parsed_receipt.expense.service_date,
            "amount": parsed_receipt.expense.amount,
            "confidence": parsed_receipt.confidence
        })
        
        message = VivianPersonality.CONFIDENCE_LOW_WARNING.format(details=details)
        
        await connection_manager.send_confirmation(
            session,
            prompt_id=f"review_{session.session_id}",
            message=message,
            actions=[
                {"id": "confirm", "label": "Looks good", "style": "primary"},
                {"id": "edit", "label": "Edit details", "style": "secondary"},
                {"id": "cancel", "label": "Cancel", "style": "danger"}
            ],
            display_data={
                "type": "receipt_review",
                "data": parsed_receipt.model_dump()
            }
        )
    
    async def _request_confirmation(self, session: ChatSession, parsed_receipt):
        """Request confirmation and status selection."""
        details = VivianPersonality.format_receipt_details({
            "provider": parsed_receipt.expense.provider,
            "service_date": parsed_receipt.expense.service_date,
            "amount": parsed_receipt.expense.amount
        })
        
        message = f"**Receipt parsed:**\n{details}\n\n{VivianPersonality.CONFIRMATION_STATUS_PROMPT}"
        
        await connection_manager.send_confirmation(
            session,
            prompt_id=f"confirm_{session.session_id}",
            message=message,
            actions=[
                {"id": "reimbursed", "label": "Already reimbursed", "style": "primary"},
                {"id": "unreimbursed", "label": "Save for later", "style": "secondary"},
                {"id": "not_eligible", "label": "Not eligible", "style": "danger"}
            ],
            display_data={
                "type": "status_selection",
                "data": parsed_receipt.model_dump()
            }
        )
    
    async def handle_action(self, session: ChatSession, action_type: str, action_data: dict):
        """Handle user action from confirmation."""
        if action_type == "confirm" or action_type in ["reimbursed", "unreimbursed", "not_eligible"]:
            # Map action to status
            status_map = {
                "confirm": "unreimbursed",
                "reimbursed": "reimbursed",
                "unreimbursed": "unreimbursed",
                "not_eligible": "not_hsa_eligible"
            }
            status = status_map.get(action_type, "unreimbursed")
            await self._save_receipt(session, status)
            
        elif action_type == "edit":
            # Handle edit flow (simplified for now)
            await connection_manager.send_text(
                session,
                "Edit functionality coming soon! Please cancel and try again with the correct file."
            )
            session.end_flow()
            
        elif action_type == "cancel":
            await connection_manager.send_text(session, "Receipt upload cancelled.")
            session.end_flow()
    
    async def _save_receipt(self, session: ChatSession, status: str):
        """Save receipt to Drive and Ledger."""
        parsed = session.current_flow.data.parsed_receipt
        if not parsed:
            await connection_manager.send_text(session, "Error: No receipt data found.")
            return
        
        temp_path = session.current_flow.data.upload_temp_path
        
        # Update flow
        session.update_flow_step("saving", "in_progress", "Saving receipt...")
        await connection_manager.send_status(
            session,
            "save_progress",
            VivianPersonality.PROGRESS_UPLOADING_DRIVE
        )
        
        try:
            # Initialize MCP client
            mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
            await mcp_client.start()
            
            # Upload to Drive
            upload_result = await mcp_client.upload_receipt_to_drive(
                temp_path,
                status
            )
            
            if not upload_result.get("success"):
                raise Exception(f"Drive upload failed: {upload_result.get('error')}")
            
            drive_file_id = upload_result["file_id"]
            
            await connection_manager.send_status(
                session,
                "save_progress",
                VivianPersonality.PROGRESS_UPDATING_LEDGER
            )
            
            # Add to ledger
            expense_dict = parsed.expense.model_dump()
            ledger_result = await mcp_client.append_to_ledger(
                expense_dict,
                status,
                drive_file_id
            )
            
            if not ledger_result.get("success"):
                raise Exception(f"Ledger update failed: {ledger_result.get('error')}")
            
            await mcp_client.stop()
            
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            
            # Get updated balance
            balance_result = await mcp_client.get_unreimbursed_balance()
            balance = balance_result.get("total_unreimbursed", 0)
            
            # Success message
            status_label = {
                "reimbursed": "Reimbursed",
                "unreimbursed": "Unreimbursed",
                "not_hsa_eligible": "Not HSA Eligible"
            }.get(status, status)
            
            message = VivianPersonality.RECEIPT_SAVED.format(
                provider=parsed.expense.provider,
                amount=parsed.expense.amount,
                status=status_label,
                balance=balance
            )
            
            await connection_manager.send_text(session, message)
            session.end_flow()
            
        except Exception as e:
            await self._handle_save_error(session, str(e))
    
    async def _handle_parse_error(self, session: ChatSession, error: str):
        """Handle parsing error."""
        await connection_manager.send_error(
            session,
            error_id=f"parse_error_{session.session_id}",
            category="parse_error",
            severity="recoverable",
            message=VivianPersonality.ERROR_PARSE_FAILED,
            details={"original_error": error},
            recovery_options=[
                {"id": "retry", "label": "Try again"},
                {"id": "cancel", "label": "Cancel"}
            ]
        )
        session.current_flow.status = FlowStatus.ERROR
    
    async def _handle_save_error(self, session: ChatSession, error: str):
        """Handle save error."""
        await connection_manager.send_error(
            session,
            error_id=f"save_error_{session.session_id}",
            category="mcp_error",
            severity="recoverable",
            message=VivianPersonality.ERROR_GENERAL.format(error=error),
            recovery_options=[
                {"id": "retry", "label": "Retry"},
                {"id": "cancel", "label": "Cancel"}
            ]
        )
        session.current_flow.status = FlowStatus.ERROR
