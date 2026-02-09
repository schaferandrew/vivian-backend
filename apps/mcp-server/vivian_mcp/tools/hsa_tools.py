"""HSA expense tools for MCP server."""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from vivian_mcp.config import Settings
from vivian_shared.helpers import (
    normalize_provider,
    normalize_title,
    normalize_header,
    escape_sheet_title,
    parse_date,
    days_between,
)


class HSAToolManager:
    """Manages HSA expense operations."""

    # Expected headers for ledger validation
    EXPECTED_HEADERS = [
        "id",
        "provider",
        "service_date",
        "paid_date",
        "amount",
        "hsa_eligible",
        "status",
        "reimbursement_date",
        "drive_file_id",
        "confidence",
        "created_at",
    ]
    
    def __init__(self):
        self.settings = Settings()
        self._sheets_service = None
        self._drive_service = None
        self._worksheet_title = None
    
    def _range_for_sheet(self, sheet_title: str, cell_range: str) -> str:
        """Build an A1 range string for a worksheet title and cell range."""
        escaped = escape_sheet_title(sheet_title)
        return f"'{escaped}'!{cell_range}"

    def _get_header_row(self, service, spreadsheet_id: str, sheet_title: str) -> list[str]:
        """Fetch header row values for A1:K1 in the target worksheet."""
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range_for_sheet(sheet_title, "A1:K1"),
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return []
        return [str(value) for value in rows[0]]

    def _headers_match_expected(self, headers: list[str]) -> bool:
        """Check whether first 11 headers match expected ledger schema."""
        if len(headers) < len(self.EXPECTED_HEADERS):
            return False
        normalized = [normalize_header(header) for header in headers[: len(self.EXPECTED_HEADERS)]]
        return normalized == self.EXPECTED_HEADERS

    def _find_matching_title(self, titles: list[str], preferred: str) -> str | None:
        """Find a worksheet title by exact or normalized match."""
        if not preferred:
            return None
        if preferred in titles:
            return preferred

        normalized_preferred = normalize_title(preferred)
        for title in titles:
            if normalize_title(title) == normalized_preferred:
                return title
        return None

    def _resolve_worksheet_title(self, service) -> str:
        """Resolve worksheet title dynamically with header validation."""
        if self._worksheet_title:
            return self._worksheet_title

        spreadsheet_id = self.settings.sheets_spreadsheet_id
        metadata = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(title))",
        ).execute()
        titles = [
            sheet.get("properties", {}).get("title")
            for sheet in metadata.get("sheets", [])
            if sheet.get("properties", {}).get("title")
        ]

        if not titles:
            raise ValueError("Google Sheet has no worksheet tabs")

        preferred_titles = []
        configured_title = (self.settings.sheets_worksheet_name or "").strip()
        if configured_title:
            preferred_titles.append(configured_title)
        if "HSA_Ledger" not in preferred_titles:
            preferred_titles.append("HSA_Ledger")

        for preferred_title in preferred_titles:
            matched_title = self._find_matching_title(titles, preferred_title)
            if not matched_title:
                continue

            headers = self._get_header_row(service, spreadsheet_id, matched_title)
            if self._headers_match_expected(headers):
                self._worksheet_title = matched_title
                return self._worksheet_title

            raise ValueError(
                "Worksheet "
                f"'{matched_title}' does not have expected ledger headers. "
                f"Expected A1:K1={self.EXPECTED_HEADERS}; found={headers}"
            )

        # Fallback: find any worksheet tab with matching headers.
        for title in titles:
            headers = self._get_header_row(service, spreadsheet_id, title)
            if self._headers_match_expected(headers):
                self._worksheet_title = title
                return self._worksheet_title

        raise ValueError(
            "No worksheet with expected ledger headers found. "
            f"Available tabs={titles}. Expected A1:K1 headers={self.EXPECTED_HEADERS}"
        )
    
    def _is_duplicate(
        self, 
        new_expense: dict, 
        existing_row: list,
        fuzzy_days: int = 3
    ) -> dict:
        """Check if new expense matches existing entry.
        
        Returns:
            dict with keys: is_duplicate (bool), match_type (str), days_difference (int or None)
        """
        # Check if row has enough columns
        if len(existing_row) < 5:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}
        
        # Normalize and compare provider names (column B = index 1)
        new_provider = normalize_provider(new_expense.get("provider", ""))
        existing_provider = normalize_provider(existing_row[1] if len(existing_row) > 1 else "")
        
        if not new_provider or not existing_provider:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}
        
        if new_provider != existing_provider:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}
        
        # Compare amounts (column E = index 4) - within $0.01 tolerance
        try:
            new_amount = float(new_expense.get("amount", 0))
            existing_amount = float(existing_row[4] if len(existing_row) > 4 else 0)
            if abs(new_amount - existing_amount) > 0.01:
                return {"is_duplicate": False, "match_type": None, "days_difference": None}
        except (ValueError, TypeError):
            return {"is_duplicate": False, "match_type": None, "days_difference": None}
        
        # Compare dates (column C = index 2)
        new_date_str = new_expense.get("service_date", "")
        existing_date_str = existing_row[2] if len(existing_row) > 2 else ""
        
        new_date = parse_date(new_date_str)
        existing_date = parse_date(existing_date_str)
        
        if not new_date or not existing_date:
            # If we can't parse dates but provider and amount match, consider it a fuzzy match
            return {
                "is_duplicate": True, 
                "match_type": "fuzzy_date", 
                "days_difference": None,
                "message": "Provider and amount match, but dates could not be compared"
            }
        
        days_diff = abs((new_date - existing_date).days)
        
        if days_diff == 0:
            return {"is_duplicate": True, "match_type": "exact", "days_difference": 0}
        elif days_diff <= fuzzy_days:
            return {
                "is_duplicate": True, 
                "match_type": "fuzzy_date", 
                "days_difference": days_diff
            }
        else:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}
    
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
    
    async def check_for_duplicates(
        self,
        expense_json: dict,
        fuzzy_days: int = 3
    ) -> str:
        """Check if expense is a duplicate of existing entries.
        
        Args:
            expense_json: Expense data with provider, service_date, amount
            fuzzy_days: Number of days to allow for fuzzy date matching (default: 3)
            
        Returns:
            JSON string with potential duplicates and recommendations
        """
        try:
            service = self._get_sheets_service()
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            worksheet_title = self._resolve_worksheet_title(service)
            
            # Fetch all existing entries
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=self._range_for_sheet(worksheet_title, "A:K")
            ).execute()
            
            rows = result.get("values", [])
            if len(rows) <= 1:
                return json.dumps({
                    "is_duplicate": False,
                    "potential_duplicates": [],
                    "recommendation": "import"
                })
            
            potential_duplicates = []
            
            # Check each existing row (skip header)
            for row in rows[1:]:
                match_result = self._is_duplicate(expense_json, row, fuzzy_days)
                
                if match_result["is_duplicate"]:
                    duplicate_info = {
                        "entry_id": row[0] if len(row) > 0 else "",
                        "provider": row[1] if len(row) > 1 else "",
                        "service_date": row[2] if len(row) > 2 else "",
                        "paid_date": row[3] if len(row) > 3 else "",
                        "amount": float(row[4]) if len(row) > 4 and row[4] else 0,
                        "hsa_eligible": row[5].lower() == "true" if len(row) > 5 and row[5] else True,
                        "status": row[6] if len(row) > 6 else "",
                        "reimbursement_date": row[7] if len(row) > 7 else "",
                        "drive_file_id": row[8] if len(row) > 8 else "",
                        "confidence": float(row[9]) if len(row) > 9 and row[9] else 0,
                        "match_type": match_result["match_type"],
                        "days_difference": match_result["days_difference"]
                    }
                    potential_duplicates.append(duplicate_info)
            
            # Determine recommendation
            if not potential_duplicates:
                recommendation = "import"
            elif all(d["match_type"] == "exact" for d in potential_duplicates):
                recommendation = "skip"
            else:
                recommendation = "review"
            
            return json.dumps({
                "is_duplicate": len(potential_duplicates) > 0,
                "potential_duplicates": potential_duplicates,
                "recommendation": recommendation,
                "total_duplicates_found": len(potential_duplicates)
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e),
                "is_duplicate": False,
                "potential_duplicates": []
            })
    
    async def append_to_ledger(
        self, 
        expense_json: dict, 
        reimbursement_status: str,
        drive_file_id: str,
        check_duplicates: bool = True,
        force_append: bool = False
    ) -> str:
        """Append expense to Google Sheets ledger.
        
        Args:
            expense_json: Expense data
            reimbursement_status: Status of reimbursement
            drive_file_id: Google Drive file ID
            check_duplicates: Whether to check for duplicates before appending
            force_append: Whether to append even if duplicates are found
        """
        try:
            service = self._get_sheets_service()
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            worksheet_title = self._resolve_worksheet_title(service)
            
            # Check for duplicates if enabled
            duplicate_check_result = None
            if check_duplicates:
                check_result_json = await self.check_for_duplicates(expense_json)
                duplicate_check_result = json.loads(check_result_json)
                
                if duplicate_check_result.get("is_duplicate") and not force_append:
                    return json.dumps({
                        "success": False,
                        "error": "Duplicate entry detected",
                        "duplicate_check": duplicate_check_result,
                        "entry_appended": False
                    })
            
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
            range_name = self._range_for_sheet(worksheet_title, "A:K")
            
            body = {
                "values": [row]
            }
            
            result = service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body=body
            ).execute()
            
            response = {
                "success": True,
                "entry_id": entry_id,
                "updated_range": result.get("updates", {}).get("updatedRange", ""),
                "entry_appended": True
            }
            
            # Include duplicate check info if it was performed
            if duplicate_check_result:
                response["duplicate_check"] = duplicate_check_result
            
            return json.dumps(response)
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e),
                "entry_appended": False
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
