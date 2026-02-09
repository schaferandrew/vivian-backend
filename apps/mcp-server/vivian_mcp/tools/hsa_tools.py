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

    @staticmethod
    def _escape_sheet_title(sheet_title: str) -> str:
        """Escape worksheet title for A1 notation."""
        return sheet_title.replace("'", "''")

    @staticmethod
    def _normalize_title(value: str) -> str:
        """Normalize worksheet title for loose matching."""
        return value.strip().lower().replace(" ", "").replace("_", "")

    @staticmethod
    def _normalize_header(value: str) -> str:
        """Normalize header cell values for comparison."""
        return (
            value.strip()
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )

    def _range_for_sheet(self, sheet_title: str, cell_range: str) -> str:
        """Build an A1 range string for a worksheet title and cell range."""
        escaped = self._escape_sheet_title(sheet_title)
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
        normalized = [self._normalize_header(header) for header in headers[: len(self.EXPECTED_HEADERS)]]
        return normalized == self.EXPECTED_HEADERS

    def _find_matching_title(self, titles: list[str], preferred: str) -> str | None:
        """Find a worksheet title by exact or normalized match."""
        if not preferred:
            return None
        if preferred in titles:
            return preferred

        normalized_preferred = self._normalize_title(preferred)
        for title in titles:
            if self._normalize_title(title) == normalized_preferred:
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
            worksheet_title = self._resolve_worksheet_title(service)
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
            
            return json.dumps({
                "success": True,
                "entry_id": entry_id,
                "worksheet_title": worksheet_title,
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
            worksheet_title = self._resolve_worksheet_title(service)
            
            # Find the row with matching ID
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=self._range_for_sheet(worksheet_title, "A:K")
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
                    "range": self._range_for_sheet(worksheet_title, f"G{target_row}"),
                    "values": [[new_status]]
                }
            ]
            
            # Update reimbursement date if provided (H = column 8)
            if reimbursement_date:
                updates.append({
                    "range": self._range_for_sheet(worksheet_title, f"H{target_row}"),
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
                "new_status": new_status,
                "worksheet_title": worksheet_title,
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
            worksheet_title = self._resolve_worksheet_title(service)
            
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=self._range_for_sheet(worksheet_title, "A:K")
            ).execute()
            
            rows = result.get("values", [])
            if len(rows) <= 1:
                return json.dumps({
                    "total_unreimbursed": 0,
                    "count": 0,
                    "worksheet_title": worksheet_title,
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
                "count": count,
                "worksheet_title": worksheet_title,
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
