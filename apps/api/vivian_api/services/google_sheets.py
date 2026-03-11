"""Google Sheets and Drive service helpers.

Builds Google credentials from per-home DB connections, then wraps common
Sheets/Drive operations used by the drive_config bootstrap and internal endpoints.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from vivian_api.services.google_integration import (
    get_google_client_id,
    get_google_client_secret,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from vivian_api.config import Settings

logger = logging.getLogger(__name__)

_TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_google_credentials(home_id: str, db: "Session", settings: "Settings"):
    """Build Google OAuth2 credentials for a home using the DB connection."""
    import google.oauth2.credentials
    from vivian_api.repositories.connection_repository import HomeConnectionRepository

    repo = HomeConnectionRepository(db)
    connection = repo.get_by_home_and_provider(
        home_id=home_id,
        provider="google",
        connection_type="drive_sheets",
    )
    if not connection:
        raise ValueError(f"Google not connected for home {home_id!r}")

    refresh_token = repo.get_decrypted_refresh_token(connection)
    client_id = get_google_client_id(settings)
    client_secret = get_google_client_secret(settings)

    return google.oauth2.credentials.Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
    )


def get_sheets_service(creds):
    """Build Google Sheets API v4 service."""
    from googleapiclient.discovery import build

    return build("sheets", "v4", credentials=creds)


def get_drive_service(creds):
    """Build Google Drive API v3 service."""
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=creds)


def append_row(sheets_svc, spreadsheet_id: str, worksheet: str, values: list) -> None:
    """Append a row to a Google Sheet worksheet."""
    sheets_svc.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{worksheet}!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [values]},
    ).execute()


def read_rows(sheets_svc, spreadsheet_id: str, worksheet: str) -> list[list[str]]:
    """Read data rows from a worksheet. Row 1 (header) is skipped."""
    result = (
        sheets_svc.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{worksheet}!A:Z")
        .execute()
    )
    rows = result.get("values", [])
    return rows[1:] if len(rows) > 1 else []


def search_drive(drive_svc, name: str, parent_id: Optional[str] = None) -> Optional[str]:
    """Search Drive for a file/folder by name. Returns file ID or None."""
    query = f"name = '{name}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    result = drive_svc.files().list(q=query, fields="files(id)", pageSize=1).execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def create_folder(drive_svc, name: str, parent_id: Optional[str] = None) -> str:
    """Create a Drive folder. Returns new folder ID."""
    metadata: dict = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = drive_svc.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def create_sheet(
    drive_svc,
    sheets_svc,
    name: str,
    parent_id: str,
    headers: list[str],
) -> str:
    """Create a Google Sheet with a header row inside a Drive folder.

    Returns the new spreadsheet ID.
    """
    # Create spreadsheet via Sheets API (ensures correct title)
    spreadsheet = sheets_svc.spreadsheets().create(
        body={"properties": {"title": name}},
        fields="spreadsheetId",
    ).execute()
    spreadsheet_id = spreadsheet["spreadsheetId"]

    # Move into the target Drive folder
    file_data = drive_svc.files().get(fileId=spreadsheet_id, fields="parents").execute()
    current_parents = ",".join(file_data.get("parents", []))
    drive_svc.files().update(
        fileId=spreadsheet_id,
        addParents=parent_id,
        removeParents=current_parents,
        fields="id",
    ).execute()

    # Write header row
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [headers]},
    ).execute()

    return spreadsheet_id
