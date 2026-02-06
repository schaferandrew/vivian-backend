"""Google Drive tools for MCP server."""

import json
from datetime import datetime
from pathlib import Path

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials

from vivian_mcp.config import Settings


class DriveToolManager:
    """Manages Google Drive operations."""
    
    def __init__(self):
        self.settings = Settings()
        self._drive_service = None
    
    def _get_drive_service(self):
        """Get Google Drive service."""
        if not self._drive_service:
            creds = Credentials(
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
            self._drive_service = build("drive", "v3", credentials=creds)
        return self._drive_service
    
    def _get_folder_id_for_status(self, status: str) -> str:
        """Get the appropriate folder ID based on reimbursement status."""
        folder_map = {
            "reimbursed": self.settings.reimbursed_folder_id,
            "unreimbursed": self.settings.unreimbursed_folder_id,
            "not_hsa_eligible": self.settings.not_eligible_folder_id
        }
        return folder_map.get(status, self.settings.unreimbursed_folder_id)
    
    async def upload_receipt(
        self,
        local_file_path: str,
        status: str,
        filename: str = None
    ) -> str:
        """Upload receipt to Google Drive."""
        try:
            service = self._get_drive_service()
            file_path = Path(local_file_path)
            
            if not file_path.exists():
                return json.dumps({
                    "success": False,
                    "error": f"File not found: {local_file_path}"
                })
            
            # Use custom filename or original
            upload_filename = filename or file_path.name
            
            # Add timestamp to filename to avoid collisions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name_without_ext = Path(upload_filename).stem
            ext = Path(upload_filename).suffix
            final_filename = f"{name_without_ext}_{timestamp}{ext}"
            
            # Get folder ID based on status
            folder_id = self._get_folder_id_for_status(status)
            
            # File metadata
            file_metadata = {
                "name": final_filename,
                "parents": [folder_id] if folder_id else []
            }
            
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
            
            return json.dumps({
                "success": True,
                "file_id": file.get("id"),
                "filename": file.get("name"),
                "web_view_link": file.get("webViewLink"),
                "folder": status
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
    
    async def move_file(self, file_id: str, new_status: str) -> str:
        """Move file to different folder based on status change."""
        try:
            service = self._get_drive_service()
            
            # Get current parents
            file = service.files().get(
                fileId=file_id,
                fields="parents"
            ).execute()
            
            current_parents = file.get("parents", [])
            
            # Get new folder ID
            new_folder_id = self._get_folder_id_for_status(new_status)
            
            if not new_folder_id:
                return json.dumps({
                    "success": False,
                    "error": f"No folder configured for status: {new_status}"
                })
            
            # Move file: add new parent, remove old parents
            service.files().update(
                fileId=file_id,
                addParents=new_folder_id,
                removeParents=",".join(current_parents),
                fields="id, parents"
            ).execute()
            
            return json.dumps({
                "success": True,
                "file_id": file_id,
                "new_status": new_status,
                "new_folder_id": new_folder_id
            })
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "error": str(e)
            })
