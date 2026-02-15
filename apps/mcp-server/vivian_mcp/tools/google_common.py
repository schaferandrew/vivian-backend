"""Shared Google Drive and Sheets utilities for MCP servers."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials


def _normalize_filter_operator(raw_operator: Any) -> str:
    """Normalize operator aliases to canonical names."""
    operator = str(raw_operator or "").strip().lower()
    aliases = {
        "eq": "equals",
        "==": "equals",
        "neq": "not_equals",
        "!=": "not_equals",
        "in_list": "in",
        "gt": "greater_than",
        ">": "greater_than",
        "gte": "greater_than_or_equal",
        ">=": "greater_than_or_equal",
        "lt": "less_than",
        "<": "less_than",
        "lte": "less_than_or_equal",
        "<=": "less_than_or_equal",
    }
    return aliases.get(operator, operator)


def _coerce_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_for_compare(value: Any, case_sensitive: bool) -> str:
    text = str(value if value is not None else "")
    return text if case_sensitive else text.lower()


SUPPORTED_FILTER_OPERATORS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "starts_with",
    "ends_with",
    "in",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
}


def _matches_column_filter(cell_value: Any, operator: str, expected_value: Any, case_sensitive: bool) -> bool:
    lhs = _normalize_for_compare(cell_value, case_sensitive)
    rhs = _normalize_for_compare(expected_value, case_sensitive)

    if operator == "equals":
        return lhs == rhs
    if operator == "not_equals":
        return lhs != rhs
    if operator == "contains":
        return rhs in lhs
    if operator == "not_contains":
        return rhs not in lhs
    if operator == "starts_with":
        return lhs.startswith(rhs)
    if operator == "ends_with":
        return lhs.endswith(rhs)
    if operator == "in":
        if not isinstance(expected_value, list):
            return False
        normalized_values = {_normalize_for_compare(item, case_sensitive) for item in expected_value}
        return lhs in normalized_values
    if operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
        lhs_num = _coerce_number(cell_value)
        rhs_num = _coerce_number(expected_value)
        if lhs_num is None or rhs_num is None:
            return False
        if operator == "greater_than":
            return lhs_num > rhs_num
        if operator == "greater_than_or_equal":
            return lhs_num >= rhs_num
        if operator == "less_than":
            return lhs_num < rhs_num
        return lhs_num <= rhs_num
    return False


def apply_column_filters(
    *,
    headers: list[Any],
    rows: list[list[Any]],
    column_filters: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Apply ANDed column filters to tabular rows."""
    if not column_filters:
        return {"success": True, "rows": rows}

    if not headers:
        return {"success": False, "error": "No headers available for column filtering", "available_columns": []}

    normalized_headers = [str(h).strip() for h in headers]
    header_index = {header.lower(): idx for idx, header in enumerate(normalized_headers)}
    available_columns = sorted(normalized_headers)

    compiled_filters: list[tuple[int, str, Any, bool]] = []

    for filt in column_filters:
        if not isinstance(filt, dict):
            return {
                "success": False,
                "error": "Each column filter must be an object",
                "available_columns": available_columns,
            }
        column = str(filt.get("column") or "").strip()
        if not column:
            return {
                "success": False,
                "error": "Each column filter requires a non-empty 'column' field",
                "available_columns": available_columns,
            }
        idx = header_index.get(column.lower())
        if idx is None:
            return {
                "success": False,
                "error": f"Unknown column '{column}'",
                "available_columns": available_columns,
            }

        operator = _normalize_filter_operator(filt.get("operator", "equals"))
        if operator not in SUPPORTED_FILTER_OPERATORS:
            return {
                "success": False,
                "error": (
                    f"Unsupported operator '{filt.get('operator')}'. "
                    f"Supported operators: {', '.join(sorted(SUPPORTED_FILTER_OPERATORS))}"
                ),
                "available_columns": available_columns,
            }

        if "value" not in filt:
            return {
                "success": False,
                "error": "Each column filter requires a 'value' field",
                "available_columns": available_columns,
            }

        compiled_filters.append(
            (idx, operator, filt.get("value"), bool(filt.get("case_sensitive", False)))
        )

    filtered_rows: list[list[Any]] = []
    for row in rows:
        keep = True
        for idx, operator, expected_value, case_sensitive in compiled_filters:
            cell_value = row[idx] if idx < len(row) else None
            if not _matches_column_filter(cell_value, operator, expected_value, case_sensitive):
                keep = False
                break
        if keep:
            filtered_rows.append(row)

    return {"success": True, "rows": filtered_rows}


class GoogleServiceMixin:
    """Mixin providing shared Google service initialization."""
    
    def __init__(self, settings: Any):
        self.settings = settings
        self._drive_service = None
        self._sheets_service = None
    
    def _get_credentials(self) -> Credentials:
        """Get Google OAuth credentials from settings."""
        return Credentials(
            token=None,
            refresh_token=self.settings.google_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
            scopes=[
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets"
            ]
        )
    
    def _get_drive_service(self):
        """Get or create Google Drive service."""
        if not self._drive_service:
            creds = self._get_credentials()
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service
    
    def _get_sheets_service(self):
        """Get or create Google Sheets service."""
        if not self._sheets_service:
            creds = self._get_credentials()
            self._sheets_service = build("sheets", "v4", credentials=creds)
        return self._sheets_service


class DriveOperationsMixin:
    """Mixin providing shared Drive operations."""
    
    async def upload_file(
        self,
        local_file_path: str,
        folder_id: str,
        filename: Optional[str] = None,
        add_timestamp: bool = True
    ) -> dict:
        """Upload a file to Google Drive.
        
        Args:
            local_file_path: Path to local file
            folder_id: Google Drive folder ID to upload to
            filename: Optional custom filename (uses original if not provided)
            add_timestamp: Whether to add timestamp to filename
            
        Returns:
            Dict with success, file_id, filename, web_view_link, error
        """
        try:
            service = self._get_drive_service()
            file_path = Path(local_file_path)
            
            if not file_path.exists():
                return {
                    "success": False,
                    "error": f"File not found: {local_file_path}"
                }
            
            # Use custom filename or original
            upload_filename = filename or file_path.name
            
            # Add timestamp to filename to avoid collisions
            if add_timestamp:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                name_without_ext = Path(upload_filename).stem
                ext = Path(upload_filename).suffix
                final_filename = f"{name_without_ext}_{timestamp}{ext}"
            else:
                final_filename = upload_filename
            
            # File metadata
            file_metadata = {
                "name": final_filename,
            }
            if folder_id:
                file_metadata["parents"] = [folder_id]
            
            # Upload file
            media = MediaFileUpload(
                str(file_path),
                mimetype="application/pdf",
                resumable=True
            )
            
            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink"
            ).execute()
            
            return {
                "success": True,
                "file_id": file.get("id"),
                "filename": file.get("name"),
                "web_view_link": file.get("webViewLink"),
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def create_folder(
        self,
        folder_name: str,
        parent_folder_id: Optional[str] = None
    ) -> dict:
        """Create a folder in Google Drive.
        
        Args:
            folder_name: Name of the folder to create
            parent_folder_id: Optional parent folder ID
            
        Returns:
            Dict with success, folder_id, name, error
        """
        try:
            service = self._get_drive_service()
            
            file_metadata = {
                "name": folder_name,
                "mimeType": "application/vnd.google-apps.folder",
            }
            
            if parent_folder_id:
                file_metadata["parents"] = [parent_folder_id]
            
            folder = service.files().create(
                body=file_metadata,
                fields="id, name"
            ).execute()
            
            return {
                "success": True,
                "folder_id": folder.get("id"),
                "name": folder.get("name"),
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_or_create_folder(
        self,
        folder_name: str,
        parent_folder_id: str
    ) -> dict:
        """Get existing folder or create if not exists.
        
        Args:
            folder_name: Name of the folder
            parent_folder_id: Parent folder ID to search in
            
        Returns:
            Dict with success, folder_id, name, created, error
        """
        try:
            service = self._get_drive_service()
            
            # Search for existing folder
            query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}' and '{parent_folder_id}' in parents and trashed=false"
            results = service.files().list(
                q=query,
                spaces="drive",
                fields="files(id, name)"
            ).execute()
            
            items = results.get("files", [])
            if items:
                return {
                    "success": True,
                    "folder_id": items[0]["id"],
                    "name": items[0]["name"],
                    "created": False,
                }
            
            # Create new folder
            create_result = await self.create_folder(folder_name, parent_folder_id)
            create_result["created"] = True
            return create_result
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def move_file(
        self,
        file_id: str,
        new_folder_id: str
    ) -> dict:
        """Move file to a different folder.
        
        Args:
            file_id: File ID to move
            new_folder_id: Destination folder ID
            
        Returns:
            Dict with success, file_id, new_folder_id, error
        """
        try:
            service = self._get_drive_service()
            
            # Get current parents
            file = service.files().get(
                fileId=file_id,
                fields="parents"
            ).execute()
            
            current_parents = file.get("parents", [])
            
            # Move file: add new parent, remove old parents
            service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=",".join(current_parents),
                fields="id, parents"
            ).execute()
            
            return {
                "success": True,
                "file_id": file_id,
                "new_folder_id": new_folder_id,
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


class SheetsOperationsMixin:
    """Mixin providing shared Sheets operations."""
    
    async def ensure_worksheet_exists(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        headers: Optional[list[str]] = None
    ) -> dict:
        """Ensure worksheet exists, create if not.
        
        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            worksheet_name: Name of worksheet to ensure
            headers: Optional headers to add if creating
            
        Returns:
            Dict with success, worksheet_exists, error
        """
        try:
            service = self._get_sheets_service()
            
            # Get spreadsheet to check if worksheet exists
            spreadsheet = service.spreadsheets().get(
                spreadsheetId=spreadsheet_id
            ).execute()
            
            existing_sheets = [
                sheet["properties"]["title"]
                for sheet in spreadsheet["sheets"]
            ]
            
            if worksheet_name in existing_sheets:
                return {
                    "success": True,
                    "worksheet_exists": True,
                }
            
            # Create new worksheet
            body = {
                "requests": [{
                    "addSheet": {
                        "properties": {
                            "title": worksheet_name,
                        }
                    }
                }]
            }
            
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=body
            ).execute()
            
            # Add headers if provided
            if headers:
                await self.append_row(spreadsheet_id, worksheet_name, headers)
            
            return {
                "success": True,
                "worksheet_exists": False,  # Was created
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def append_row(
        self,
        spreadsheet_id: str,
        worksheet_name: str,
        row_data: list[Any]
    ) -> dict:
        """Append a row to a worksheet.
        
        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            worksheet_name: Name of worksheet
            row_data: List of values to append
            
        Returns:
            Dict with success, row_index, error
        """
        try:
            service = self._get_sheets_service()
            
            # Escape single quotes in sheet title
            escaped_title = worksheet_name.replace("'", "''")
            range_name = f"'{escaped_title}'!A1"
            
            body = {
                "values": [row_data]
            }
            
            result = service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
            
            updates = result.get("updates", {})
            
            return {
                "success": True,
                "row_index": updates.get("updatedRange", ""),
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_all_rows(
        self,
        spreadsheet_id: str,
        worksheet_name: str
    ) -> dict:
        """Get all rows from a worksheet.
        
        Args:
            spreadsheet_id: Google Sheets spreadsheet ID
            worksheet_name: Name of worksheet
            
        Returns:
            Dict with success, headers, rows, error
        """
        try:
            service = self._get_sheets_service()
            
            # Escape single quotes in sheet title
            escaped_title = worksheet_name.replace("'", "''")
            range_name = f"'{escaped_title}'"
            
            result = service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id,
                range=range_name
            ).execute()
            
            values = result.get("values", [])
            
            if not values:
                return {
                    "success": True,
                    "headers": [],
                    "rows": [],
                }
            
            headers = values[0] if values else []
            rows = values[1:] if len(values) > 1 else []
            
            return {
                "success": True,
                "headers": headers,
                "rows": rows,
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
