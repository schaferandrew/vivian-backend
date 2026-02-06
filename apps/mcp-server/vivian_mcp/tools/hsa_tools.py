"""HSA expense tools for MCP server."""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from vivian_mcp.config import Settings


class HSAToolManager:
    """Manages HSA expense operations."""
    
    def __init__(self):
        self.settings = Settings()
        self._sheets_service = None
        self._drive_service = None
    
    def _get_sheets_service(self):
        """Get Google Sheets service."""
        if not self._sheets_service:
            creds = Credentials(
                token=None,
                refresh_token=self.settings.google_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=self.settings.google_client_id,
                client_secret=self.settings.google_client_secret,
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive"
                ]
            )
            self._sheets_service = build("sheets", "v4", credentials=creds)
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._sheets_service
    
    async def parse_receipt(self, pdf_path: str) -> str:
        """Parse receipt PDF and return structured data.
        
        Note: Actual parsing happens in the API layer using OpenRouter.
        This tool is for compatibility with MCP protocol.
        """
        # The actual parsing is done by the API layer
        # This returns a placeholder indicating the file is ready
        return json.dumps({
            "status": "ready_for_parsing",
            "pdf_path": pdf_path,
            "message": "Use API layer with OpenRouter for actual parsing"
        })
    
    async def append_to_ledger(
        self, 
        expense_json: dict, 
        reimbursement_status: str,
        drive_file_id: str
    ) -> str:
        """Append expense to Google Sheets ledger."""
        try:
            service = self._get_sheets_service()
            
            # Generate unique ID
            entry_id = str(uuid.uuid4())[:8]
            
            # Get current timestamp
            created_at = datetime.utcnow().isoformat()
            
            # Prepare row data
            row = [
                entry_id,
                expense_json.get("provider", ""),
                expense_json.get("service_date", ""),
                expense_json.get("paid_date", ""),
                expense_json.get("amount", 0),
                expense_json.get("hsa_eligible", True),
                reimbursement_status,
                expense_json.get("reimbursement_date", ""),
                drive_file_id,
                expense_json.get("confidence", 0),
                created_at
            ]
            
            # Append to sheet
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            range_name = "HSA_Ledger!A:K"
            
            body = {
                "values": [row]
            }
            
            result = service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            
            return json.dumps({
                "success": True,
                "entry_id": entry_id,
                "updated_range": result.get("updates", {}).get("updatedRange", "")
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    async def update_status(
        self, 
        expense_id: str, 
        new_status: str,
        reimbursement_date: Optional[str] = None
    ) -> str:
        """Update reimbursement status of an expense."""
        try:
            service = self._get_sheets_service()
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            
            # Find the row with matching ID
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="HSA_Ledger!A:K"
            ).execute()
            
            rows = result.get("values", [])
            
            # Find row index (0-indexed, skip header)
            target_row = None
            for i, row in enumerate(rows[1:], start=2):  # Start at 2 (1-indexed, after header)
                if row and row[0] == expense_id:
                    target_row = i
                    break
            
            if not target_row:
                return json.dumps({
                    "success": False,
                    "error": f"Expense ID {expense_id} not found"
                })
            
            # Update status column (G = column 7)
            updates = [
                {
                    "range": f"HSA_Ledger!G{target_row}",
                    "values": [[new_status]]
                }
            ]
            
            # Update reimbursement date if provided (H = column 8)
            if reimbursement_date:
                updates.append({
                    "range": f"HSA_Ledger!H{target_row}",
                    "values": [[reimbursement_date]]
                })
            
            body = {
                "valueInputOption": "USER_ENTERED",
                "data": updates
            }
            
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body
            ).execute()
            
            return json.dumps({
                "success": True,
                "expense_id": expense_id,
                "new_status": new_status
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    async def get_unreimbursed_balance(self) -> str:
        """Calculate total unreimbursed expenses."""
        try:
            service = self._get_sheets_service()
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range="HSA_Ledger!A:K"
            ).execute()
            
            rows = result.get("values", [])
            if len(rows) <= 1:
                return json.dumps({
                    "total_unreimbursed": 0,
                    "count": 0
                })
            
            total = 0
            count = 0
            
            # Skip header row, process data
            for row in rows[1:]:
                if len(row) >= 7 and row[6] == "unreimbursed":  # Column G = status
                    try:
                        amount = float(row[4])  # Column E = amount
                        total += amount
                        count += 1
                    except (ValueError, IndexError):
                        continue
            
            return json.dumps({
                "total_unreimbursed": round(total, 2),
                "count": count
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    async def bulk_import(
        self, 
        directory_path: str,
        reimbursement_status_override: Optional[str] = None
    ) -> str:
        """Bulk import receipts from directory."""
        try:
            directory = Path(directory_path)
            pdf_files = list(directory.glob("*.pdf"))
            
            return json.dumps({
                "total_files": len(pdf_files),
                "directory": str(directory),
                "message": "Use API layer with OpenRouter for parsing and Drive upload",
                "files": [f.name for f in pdf_files]
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
