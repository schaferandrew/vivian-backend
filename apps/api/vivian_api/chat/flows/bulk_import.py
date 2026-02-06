"""Bulk import flow handler."""

import os
from pathlib import Path
from typing import List

from vivian_api.chat.session import ChatSession, FlowType, FlowStatus
from vivian_api.chat.connection import connection_manager
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.receipt_parser import OpenRouterService
from vivian_api.services.mcp_client import MCPClient
from vivian_shared.models import ExpenseSchema


class BulkImportFlow:
    """Handles bulk import flow (desktop and browser methods)."""
    
    def __init__(self):
        self.parser = OpenRouterService()
    
    async def start(self, session: ChatSession):
        """Start the bulk import flow."""
        session.start_flow(FlowType.BULK_IMPORT)
        await connection_manager.send_confirmation(
            session,
            prompt_id=f"import_method_{session.session_id}",
            message=VivianPersonality.BULK_IMPORT_METHOD_PROMPT,
            actions=[
                {"id": "desktop", "label": "Desktop import (folder path)", "style": "primary"},
                {"id": "browser", "label": "Browser upload (drag & drop)", "style": "secondary"}
            ]
        )
    
    async def handle_method_selection(self, session: ChatSession, method: str):
        """Handle import method selection."""
        if not session.current_flow:
            return
        
        session.current_flow.data.import_method = method
        session.update_flow_step("method_selected", "completed")
        
        if method == "desktop":
            await connection_manager.send_text(
                session,
                VivianPersonality.BULK_IMPORT_DESKTOP_PROMPT
            )
            # Wait for user to provide path in next message
        else:
            await connection_manager.send_text(
                session,
                VivianPersonality.BULK_IMPORT_BROWSER_PROMPT
            )
            # Wait for file uploads
    
    async def handle_desktop_path(self, session: ChatSession, directory_path: str):
        """Handle desktop import with filesystem path."""
        if not session.current_flow or session.current_flow.data.import_method != "desktop":
            return
        
        # Validate path
        path = Path(directory_path).expanduser().resolve()
        
        if not path.exists():
            await connection_manager.send_error(
                session,
                error_id=f"invalid_path_{session.session_id}",
                category="validation_error",
                severity="user_fixable",
                message=f"I couldn't find that folder: {directory_path}\n\nPlease check the path and try again.",
                recovery_options=[
                    {"id": "retry_path", "label": "Try a different path"},
                    {"id": "switch_browser", "label": "Switch to browser upload"}
                ]
            )
            return
        
        if not path.is_dir():
            await connection_manager.send_error(
                session,
                error_id=f"not_directory_{session.session_id}",
                category="validation_error",
                severity="user_fixable",
                message=f"That path is not a directory: {directory_path}",
                recovery_options=[
                    {"id": "retry_path", "label": "Try again"}
                ]
            )
            return
        
        # Find PDF files
        pdf_files = list(path.glob("*.pdf"))
        
        if not pdf_files:
            await connection_manager.send_text(
                session,
                f"I didn't find any PDF files in {directory_path}. Would you like to try a different folder?"
            )
            return
        
        session.current_flow.data.directory_path = str(path)
        session.current_flow.data.uploaded_files = [str(f) for f in pdf_files]
        
        # Confirm before processing
        await connection_manager.send_confirmation(
            session,
            prompt_id=f"confirm_bulk_{session.session_id}",
            message=f"I found **{len(pdf_files)} PDF files** in that folder.\n\nHow should I mark these receipts?",
            actions=[
                {"id": "all_unreimbursed", "label": "All unreimbursed", "style": "primary"},
                {"id": "all_reimbursed", "label": "All reimbursed", "style": "secondary"},
                {"id": "ask_each", "label": "Ask for each one", "style": "secondary"}
            ]
        )
    
    async def handle_browser_files(self, session: ChatSession, file_paths: List[str]):
        """Handle browser file uploads."""
        if not session.current_flow or session.current_flow.data.import_method != "browser":
            return
        
        session.current_flow.data.uploaded_files = file_paths
        
        await connection_manager.send_confirmation(
            session,
            prompt_id=f"confirm_bulk_{session.session_id}",
            message=f"I received **{len(file_paths)} files**. How should I mark these receipts?",
            actions=[
                {"id": "all_unreimbursed", "label": "All unreimbursed", "style": "primary"},
                {"id": "all_reimbursed", "label": "All reimbursed", "style": "secondary"},
                {"id": "ask_each", "label": "Ask for each one", "style": "secondary"}
            ]
        )
    
    async def process_files(self, session: ChatSession, status_override: str):
        """Process all files in the import."""
        if not session.current_flow:
            return
        
        files = session.current_flow.data.uploaded_files
        total = len(files)
        successful = 0
        failed = 0
        total_amount = 0.0
        failed_files = []
        
        session.update_flow_step("processing", "in_progress")
        
        # Initialize MCP client
        mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
        await mcp_client.start()
        
        try:
            for i, file_path in enumerate(files):
                # Progress update
                await connection_manager.send_status(
                    session,
                    "parse_progress",
                    VivianPersonality.PROGRESS_IMPORTING.format(current=i+1, total=total),
                    progress={"current": i+1, "total": total, "percent": int((i/total)*100)},
                    details={"current_file": Path(file_path).name}
                )
                
                try:
                    # Parse receipt
                    result = await self.parser.parse_receipt(file_path)
                    
                    if not result.get("success"):
                        failed += 1
                        failed_files.append((Path(file_path).name, result.get("error", "Parse failed")))
                        continue
                    
                    parsed_data = result["parsed_data"]
                    
                    # Handle "ask_each" mode
                    if status_override == "ask_each":
                        # This would require a more complex async flow
                        # For now, default to unreimbursed
                        status = "unreimbursed"
                    else:
                        status = status_override
                    
                    # Upload to Drive
                    upload_result = await mcp_client.upload_receipt_to_drive(file_path, status)
                    
                    if not upload_result.get("success"):
                        failed += 1
                        failed_files.append((Path(file_path).name, upload_result.get("error", "Upload failed")))
                        continue
                    
                    drive_file_id = upload_result["file_id"]
                    
                    # Add to ledger
                    expense = ExpenseSchema(
                        provider=parsed_data.get("provider", "Unknown"),
                        service_date=parsed_data.get("service_date"),
                        paid_date=parsed_data.get("paid_date"),
                        amount=float(parsed_data.get("amount", 0)),
                        hsa_eligible=parsed_data.get("hsa_eligible", True)
                    )
                    
                    ledger_result = await mcp_client.append_to_ledger(
                        expense.model_dump(),
                        status,
                        drive_file_id
                    )
                    
                    if not ledger_result.get("success"):
                        failed += 1
                        failed_files.append((Path(file_path).name, ledger_result.get("error", "Ledger failed")))
                        continue
                    
                    successful += 1
                    total_amount += expense.amount
                    
                    # Clean up temp file if browser upload
                    if session.current_flow.data.import_method == "browser":
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    
                except Exception as e:
                    failed += 1
                    failed_files.append((Path(file_path).name, str(e)))
                    
                    if not session.current_flow.data.skip_errors:
                        # Pause and ask user
                        await self._handle_processing_error(session, Path(file_path).name, str(e))
                        return
            
            # Get final balance
            balance_result = await mcp_client.get_unreimbursed_balance()
            balance = balance_result.get("total_unreimbursed", 0)
            
            # Build result details
            details = ""
            if failed_files:
                details = "\n\n**Failed files:**\n" + "\n".join([f"• {name}: {error}" for name, error in failed_files[:5]])
                if len(failed_files) > 5:
                    details += f"\n... and {len(failed_files) - 5} more"
            
            # Success message
            message = VivianPersonality.BULK_IMPORT_COMPLETE.format(
                successful=successful,
                total=total,
                failed=failed,
                total_amount=total_amount,
                details=details,
                balance=balance
            )
            
            await connection_manager.send_text(session, message)
            session.end_flow()
            
        finally:
            await mcp_client.stop()
    
    async def _handle_processing_error(self, session: ChatSession, filename: str, error: str):
        """Handle error during bulk processing with skip_errors=False."""
        await connection_manager.send_error(
            session,
            error_id=f"bulk_error_{session.session_id}",
            category="parse_error",
            severity="recoverable",
            message=f"I had trouble processing **{filename}**: {error}",
            recovery_options=[
                {"id": "skip_and_continue", "label": "Skip this file and continue"},
                {"id": "retry_file", "label": "Retry this file"},
                {"id": "stop_import", "label": "Stop the import"}
            ]
        )
        session.current_flow.status = FlowStatus.PAUSED
