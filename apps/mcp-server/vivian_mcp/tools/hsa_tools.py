"""HSA expense tools for MCP server."""

import json
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

from vivian_mcp.config import Settings

try:
    from vivian_shared.helpers import (
        normalize_provider,
        normalize_title,
        normalize_header,
        escape_sheet_title,
        parse_date,
        days_between,
    )
except Exception:
    # Fallback helpers keep MCP server functional if shared helpers are unavailable.
    def normalize_provider(provider: str) -> str:
        if not provider:
            return ""
        normalized = provider.lower().strip()
        suffixes = [
            r"\s+llc\.?$",
            r"\s+inc\.?$",
            r"\s+corp\.?$",
            r"\s+co\.?$",
            r"\s+ltd\.?$",
            r"\s+md\.?$",
            r"\s+do\.?$",
            r"\s+dds\.?$",
            r"\s+dmd\.?$",
            r"\s+phd\.?$",
            r"\s+np\.?$",
            r"\s+pa\.?$",
            r"\s+rn\.?$",
        ]
        for suffix in suffixes:
            normalized = re.sub(suffix, "", normalized)
        return " ".join(normalized.split())

    def normalize_title(value: str) -> str:
        return value.strip().lower().replace(" ", "").replace("_", "")

    def normalize_header(value: str) -> str:
        return value.strip().lower().replace(" ", "_").replace("-", "_")

    def escape_sheet_title(sheet_title: str) -> str:
        return sheet_title.replace("'", "''")

    def parse_date(date_str: str):
        if not date_str:
            return None
        formats = [
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%m/%d/%Y",
            "%m-%d-%Y",
            "%d/%m/%Y",
            "%d-%m-%Y",
            "%b %d, %Y",
            "%B %d, %Y",
            "%d %b %Y",
            "%Y%m%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(str(date_str).strip(), fmt)
            except ValueError:
                continue
        return None

    def days_between(date1: str, date2: str):
        d1 = parse_date(date1)
        d2 = parse_date(date2)
        if not d1 or not d2:
            return None
        return abs((d2 - d1).days)


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
    PROVIDER_TOKEN_STOPWORDS = {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "pllc",
        "pc",
        "the",
    }
    
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

    def _provider_tokens(self, provider: str) -> list[str]:
        """Tokenize provider for fuzzy matching."""
        normalized = normalize_provider(provider)
        if not normalized:
            return []
        normalized = re.sub(r"[^a-z0-9]+", " ", normalized.lower())
        return [
            token
            for token in normalized.split()
            if token and token not in self.PROVIDER_TOKEN_STOPWORDS
        ]

    def _provider_match_details(self, new_provider: str, existing_provider: str) -> dict:
        """Return provider similarity details for duplicate checks."""
        normalized_new = normalize_provider(new_provider)
        normalized_existing = normalize_provider(existing_provider)

        if not normalized_new or not normalized_existing:
            return {
                "matches": False,
                "exact": False,
                "score": 0.0,
                "reason": "missing_provider",
            }

        if normalized_new == normalized_existing:
            return {
                "matches": True,
                "exact": True,
                "score": 1.0,
                "reason": "provider_exact",
            }

        new_tokens = self._provider_tokens(normalized_new)
        existing_tokens = self._provider_tokens(normalized_existing)
        if new_tokens and existing_tokens:
            new_set = set(new_tokens)
            existing_set = set(existing_tokens)
            token_intersection = len(new_set.intersection(existing_set))
            token_overlap = token_intersection / max(1, min(len(new_set), len(existing_set)))

            if (new_set <= existing_set or existing_set <= new_set) and token_overlap >= 1.0:
                return {
                    "matches": True,
                    "exact": False,
                    "score": 0.95,
                    "reason": "provider_token_subset",
                }

            token_ratio = SequenceMatcher(
                None,
                " ".join(sorted(new_set)),
                " ".join(sorted(existing_set)),
            ).ratio()
            if token_overlap >= 0.8 and token_ratio >= 0.72:
                return {
                    "matches": True,
                    "exact": False,
                    "score": max(token_overlap, token_ratio),
                    "reason": "provider_token_overlap",
                }

        string_ratio = SequenceMatcher(None, normalized_new, normalized_existing).ratio()
        if string_ratio >= 0.86:
            return {
                "matches": True,
                "exact": False,
                "score": string_ratio,
                "reason": "provider_string_similarity",
            }

        return {
            "matches": False,
            "exact": False,
            "score": string_ratio,
            "reason": "provider_mismatch",
        }

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

        # Compare provider names (column B = index 1) with conservative fuzzy matching.
        provider_match = self._provider_match_details(
            new_expense.get("provider", ""),
            existing_row[1] if len(existing_row) > 1 else "",
        )
        if not provider_match["matches"]:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}

        # Parse dates first (column C = index 2) so we can gate close-amount matching.
        new_date_str = new_expense.get("service_date", "")
        existing_date_str = existing_row[2] if len(existing_row) > 2 else ""
        new_date = parse_date(new_date_str)
        existing_date = parse_date(existing_date_str)
        days_diff = abs((new_date - existing_date).days) if new_date and existing_date else None

        # Compare amounts (column E = index 4).
        try:
            new_amount = float(new_expense.get("amount", 0))
            existing_amount = float(existing_row[4] if len(existing_row) > 4 else 0)
            amount_diff = abs(new_amount - existing_amount)
        except (ValueError, TypeError):
            return {"is_duplicate": False, "match_type": None, "days_difference": None}

        amount_exact = amount_diff <= 0.01
        amount_close_tolerance = max(
            0.5,
            round(max(abs(new_amount), abs(existing_amount), 1.0) * 0.02, 2),
        )
        allow_close_amount = (
            days_diff is not None
            and (
                (provider_match["exact"] and days_diff == 0)
                or (not provider_match["exact"] and days_diff <= fuzzy_days)
            )
        )
        amount_close = allow_close_amount and amount_diff <= amount_close_tolerance
        if not amount_exact and not amount_close:
            return {"is_duplicate": False, "match_type": None, "days_difference": None}

        if not new_date or not existing_date:
            # Without comparable dates, require exact amount match to avoid false positives.
            if not amount_exact:
                return {"is_duplicate": False, "match_type": None, "days_difference": None}
            return {
                "is_duplicate": True, 
                "match_type": "fuzzy_date", 
                "days_difference": None,
                "message": (
                    "Provider and amount match, but dates could not be compared"
                    if provider_match["exact"]
                    else "Similar provider and exact amount match, but dates could not be compared"
                )
            }

        if days_diff == 0 and provider_match["exact"] and amount_exact:
            return {"is_duplicate": True, "match_type": "exact", "days_difference": 0}

        if days_diff <= fuzzy_days:
            if provider_match["exact"] and amount_exact:
                message = "Provider and amount match with close service dates"
            elif provider_match["exact"] and not amount_exact:
                message = f"Provider/date match and amount differs by ${amount_diff:.2f}"
            elif not provider_match["exact"] and amount_exact:
                message = "Similar provider with matching amount and close service dates"
            else:
                message = f"Similar provider/date and close amount (difference ${amount_diff:.2f})"
            return {
                "is_duplicate": True, 
                "match_type": "fuzzy_date", 
                "days_difference": days_diff,
                "message": message,
            }

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

    def _get_folder_id_for_status(self, status: str) -> str:
        """Get Drive folder ID for status."""
        folder_map = {
            "reimbursed": self.settings.reimbursed_folder_id,
            "unreimbursed": self.settings.unreimbursed_folder_id,
            "not_hsa_eligible": self.settings.not_eligible_folder_id,
        }
        return folder_map.get(status, self.settings.unreimbursed_folder_id)

    def _upload_receipt_file(
        self,
        local_file_path: str,
        status: str,
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload one receipt file to Drive."""
        try:
            service = self._drive_service
            if service is None:
                self._get_sheets_service()
                service = self._drive_service
            if service is None:
                return {"success": False, "error": "Drive service unavailable"}

            file_path = Path(local_file_path)
            if not file_path.exists():
                return {"success": False, "error": f"File not found: {local_file_path}"}

            upload_filename = filename or file_path.name
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name_without_ext = Path(upload_filename).stem
            ext = Path(upload_filename).suffix
            final_filename = f"{name_without_ext}_{timestamp}{ext}"

            folder_id = self._get_folder_id_for_status(status)
            file_metadata = {
                "name": final_filename,
                "parents": [folder_id] if folder_id else [],
            }

            media = MediaFileUpload(str(file_path), resumable=True)
            created = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink",
            ).execute()

            return {
                "success": True,
                "file_id": created.get("id"),
                "filename": created.get("name"),
                "web_view_link": created.get("webViewLink"),
                "folder": status,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _duplicate_info_from_row(self, row: list, match_result: dict) -> dict:
        """Build duplicate info payload from an existing ledger row."""
        return {
            "entry_id": row[0] if len(row) > 0 else "",
            "provider": row[1] if len(row) > 1 else "",
            "service_date": row[2] if len(row) > 2 else "",
            "paid_date": row[3] if len(row) > 3 else "",
            "amount": float(row[4]) if len(row) > 4 and row[4] not in ("", None) else 0,
            "hsa_eligible": (
                row[5].lower() == "true"
                if len(row) > 5 and isinstance(row[5], str)
                else bool(row[5]) if len(row) > 5
                else True
            ),
            "status": row[6] if len(row) > 6 else "",
            "reimbursement_date": row[7] if len(row) > 7 else "",
            "drive_file_id": row[8] if len(row) > 8 else "",
            "confidence": float(row[9]) if len(row) > 9 and row[9] not in ("", None) else 0,
            "match_type": match_result.get("match_type"),
            "days_difference": match_result.get("days_difference"),
            "message": match_result.get("message"),
        }

    def _collect_duplicates(
        self,
        expense_json: dict,
        existing_rows: list[list],
        fuzzy_days: int = 3,
    ) -> list[dict]:
        """Collect duplicate matches against existing rows."""
        duplicates: list[dict] = []
        for row in existing_rows:
            match_result = self._is_duplicate(expense_json, row, fuzzy_days)
            if match_result.get("is_duplicate"):
                duplicates.append(self._duplicate_info_from_row(row, match_result))
        return duplicates

    def _build_ledger_row(
        self,
        expense_json: dict,
        reimbursement_status: str,
        drive_file_id: str,
    ) -> tuple[str, list]:
        """Build one ledger row in A:K order and return (entry_id, row)."""
        entry_id = str(uuid.uuid4())[:8]
        created_at = datetime.utcnow().isoformat()
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
            created_at,
        ]
        return entry_id, row
    
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
                        "days_difference": match_result["days_difference"],
                        "message": match_result.get("message"),
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

    async def bulk_import_receipts(
        self,
        receipts: list[dict],
        check_duplicates: bool = True,
        force_append: bool = False,
        fuzzy_days: int = 3,
    ) -> str:
        """Bulk import parsed receipts with per-file Drive upload and batched ledger append."""
        try:
            service = self._get_sheets_service()
            spreadsheet_id = self.settings.sheets_spreadsheet_id
            worksheet_title = self._resolve_worksheet_title(service)

            fetch_result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=self._range_for_sheet(worksheet_title, "A:K"),
            ).execute()
            rows = fetch_result.get("values", [])
            existing_rows = rows[1:] if len(rows) > 1 else []

            pending_rows: list[list] = []
            pending_meta: list[dict] = []
            results: list[dict] = []
            total_amount = 0.0

            for item in receipts:
                local_file_path = item.get("local_file_path", "")
                expense_json = item.get("expense_json") or {}
                reimbursement_status = item.get("reimbursement_status", "unreimbursed")
                filename = item.get("filename") or Path(local_file_path).name

                if not local_file_path:
                    results.append({
                        "filename": filename or "unknown",
                        "local_file_path": local_file_path,
                        "temp_file_path": local_file_path,
                        "status": "failed",
                        "error": "Missing local_file_path",
                    })
                    continue

                duplicate_info: list[dict] = []
                if check_duplicates:
                    duplicate_info = self._collect_duplicates(expense_json, existing_rows, fuzzy_days)
                    if duplicate_info and not force_append:
                        has_exact = any(d.get("match_type") == "exact" for d in duplicate_info)
                        results.append({
                            "filename": filename,
                            "local_file_path": local_file_path,
                            "temp_file_path": local_file_path,
                            "status": "duplicate_exact" if has_exact else "duplicate_fuzzy",
                            "duplicate_info": duplicate_info,
                            "error": "Duplicate entry detected",
                        })
                        continue

                upload_result = self._upload_receipt_file(local_file_path, reimbursement_status, filename)
                if not upload_result.get("success"):
                    results.append({
                        "filename": filename,
                        "local_file_path": local_file_path,
                        "temp_file_path": local_file_path,
                        "status": "failed",
                        "error": f"Drive upload failed: {upload_result.get('error')}",
                    })
                    continue

                drive_file_id = upload_result.get("file_id", "")
                entry_id, row = self._build_ledger_row(expense_json, reimbursement_status, drive_file_id)

                pending_rows.append(row)
                pending_meta.append(
                    {
                        "entry_id": entry_id,
                        "filename": filename,
                        "local_file_path": local_file_path,
                        "temp_file_path": local_file_path,
                        "drive_file_id": drive_file_id,
                        "amount": float(expense_json.get("amount", 0) or 0),
                    }
                )
                # Include pending row for duplicate checks within the same batch.
                existing_rows.append(row)

            if pending_rows:
                try:
                    service.spreadsheets().values().append(
                        spreadsheetId=spreadsheet_id,
                        range=self._range_for_sheet(worksheet_title, "A:K"),
                        valueInputOption="USER_ENTERED",
                        body={"values": pending_rows},
                    ).execute()

                    for meta in pending_meta:
                        total_amount += meta["amount"]
                        results.append(
                            {
                                "filename": meta["filename"],
                                "local_file_path": meta["local_file_path"],
                                "temp_file_path": meta["temp_file_path"],
                                "status": "imported",
                                "entry_id": meta["entry_id"],
                                "drive_file_id": meta["drive_file_id"],
                            }
                        )
                except Exception as e:
                    for meta in pending_meta:
                        results.append(
                            {
                                "filename": meta["filename"],
                                "local_file_path": meta["local_file_path"],
                                "temp_file_path": meta["temp_file_path"],
                                "status": "failed",
                                "error": f"Ledger batch append failed: {str(e)}",
                                "drive_file_id": meta["drive_file_id"],
                            }
                        )

            imported_count = sum(1 for r in results if r.get("status") == "imported")
            failed_count = sum(1 for r in results if r.get("status") in {"failed", "duplicate_exact", "duplicate_fuzzy"})

            return json.dumps(
                {
                    "success": imported_count > 0,
                    "imported_count": imported_count,
                    "failed_count": failed_count,
                    "total_amount": round(total_amount, 2),
                    "results": results,
                }
            )
        except Exception as e:
            return json.dumps(
                {
                    "success": False,
                    "error": str(e),
                    "imported_count": 0,
                    "failed_count": len(receipts),
                    "results": [],
                }
            )
    
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
