"""MCP client service for communicating with MCP server."""

import json
from contextlib import AbstractAsyncContextManager
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent


class MCPClientError(Exception):
    """Raised when MCP communication fails."""


class MCPClient:
    """Client for communicating with MCP server via stdio."""
    
    def __init__(
        self,
        server_command: list[str],
        process_env: Optional[dict[str, str]] = None,
        server_path_override: Optional[str] = None,
    ):
        self.server_command = server_command
        self.process_env = process_env
        self.server_path_override = server_path_override
        self._session: Optional[ClientSession] = None
        self._stdio_cm: Optional[AbstractAsyncContextManager] = None
        self._session_started = False
    
    async def start(self):
        """Start the MCP server process."""
        if self._session_started and self._session:
            return
        if self.process_env is not None:
            clean_env = dict(self.process_env)
        else:
            # Build fresh MCP env on each start so OAuth changes are picked up.
            from vivian_api.config import Settings
            from vivian_api.services.google_integration import build_mcp_env

            clean_env = build_mcp_env(Settings())

        if not self.server_command:
            raise MCPClientError("MCP server command is empty")

        mcp_cwd = self.server_path_override or "/tmp"

        params = StdioServerParameters(
            command=self.server_command[0],
            args=self.server_command[1:],
            env=clean_env,
            # Default away from /app/.env unless explicit server path provided.
            cwd=mcp_cwd,
        )
        try:
            self._stdio_cm = stdio_client(params)
            read_stream, write_stream = await self._stdio_cm.__aenter__()

            session = ClientSession(read_stream, write_stream)
            self._session = await session.__aenter__()
            await self._session.initialize()
            self._session_started = True
        except Exception as e:
            await self.stop()
            raise MCPClientError(f"Failed to start MCP session: {e}") from e
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        last_error: Optional[Exception] = None
        for _ in range(2):
            try:
                if not self._session_started or not self._session:
                    await self.start()
                if not self._session:
                    raise MCPClientError("MCP session is unavailable")

                result = await self._session.call_tool(tool_name, arguments or {})
                content: list[dict[str, Any]] = []
                for part in result.content:
                    if isinstance(part, TextContent):
                        content.append({"type": "text", "text": part.text})
                    else:
                        text = getattr(part, "text", None)
                        if text is not None:
                            content.append({"type": "text", "text": str(text)})
                if getattr(result, "isError", False):
                    text = content[0]["text"] if content else "unknown MCP error"
                    raise MCPClientError(f"MCP tool '{tool_name}' returned error: {text}")

                return {"content": content}
            except Exception as e:
                last_error = e
                await self.stop()
                continue

        raise MCPClientError(f"MCP tool call failed after retry ({tool_name}): {last_error}")
    
    async def upload_receipt_to_drive(
        self, 
        local_file_path: str, 
        status: str,
        filename: Optional[str] = None
    ) -> dict:
        """Upload receipt to Google Drive."""
        payload = {
            "local_file_path": local_file_path,
            "status": status,
        }
        # Keep payload compatible with stricter MCP schemas (no null optional fields).
        if filename:
            payload["filename"] = filename

        result = await self.call_tool("upload_receipt_to_drive", payload)
        
        # Parse the text content from MCP response
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
    async def append_to_ledger(
        self,
        expense_json: dict,
        status: str,
        drive_file_id: str,
        check_duplicates: bool = True,
        force_append: bool = False
    ) -> dict:
        """Append expense to ledger."""
        payload = {
            "expense_json": expense_json,
            "reimbursement_status": status,
            "drive_file_id": drive_file_id,
        }
        # Keep payload compatible with stricter/older MCP schemas.
        if not check_duplicates:
            payload["check_duplicates"] = False
        if force_append:
            payload["force_append"] = True

        result = await self.call_tool("append_expense_to_ledger", payload)
        
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)

    async def upload_charitable_receipt_to_drive(
        self,
        local_file_path: str,
        donation_year: Optional[int] = None,
        filename: Optional[str] = None,
    ) -> dict:
        """Upload charitable receipt to Google Drive."""
        payload = {
            "local_file_path": local_file_path,
        }
        if donation_year:
            payload["donation_year"] = donation_year
        if filename:
            payload["filename"] = filename

        result = await self.call_tool("upload_charitable_receipt_to_drive", payload)
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)

    async def append_charitable_donation_to_ledger(
        self,
        donation_json: dict,
        drive_file_id: str,
        force_append: bool = False,
    ) -> dict:
        """Append charitable donation to ledger."""
        payload = {
            "donation_json": donation_json,
            "drive_file_id": drive_file_id,
        }
        if force_append:
            payload["force_append"] = True

        result = await self.call_tool("append_charitable_donation_to_ledger", payload)
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
    async def check_for_duplicates(
        self,
        expense_json: dict,
        fuzzy_days: int = 3
    ) -> dict:
        """Check for duplicate entries in the ledger.
        
        Args:
            expense_json: Expense data with provider, service_date, amount
            fuzzy_days: Number of days to allow for fuzzy date matching
            
        Returns:
            Dict with is_duplicate, potential_duplicates, recommendation
        """
        payload = {"expense_json": expense_json}
        # Keep payload compatible with stricter/older MCP schemas.
        if fuzzy_days != 3:
            payload["fuzzy_days"] = fuzzy_days
        result = await self.call_tool("check_for_duplicates", payload)
        
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)

    async def bulk_import_receipts(
        self,
        receipts: list[dict],
        check_duplicates: bool = True,
        force_append: bool = False,
        fuzzy_days: int = 3,
    ) -> dict:
        """Bulk import parsed receipts: per-file Drive upload + batched ledger write."""
        payload = {"receipts": receipts}
        # Keep payload compatible with stricter/older schemas.
        if not check_duplicates:
            payload["check_duplicates"] = False
        if force_append:
            payload["force_append"] = True
        if fuzzy_days != 3:
            payload["fuzzy_days"] = fuzzy_days

        result = await self.call_tool("bulk_import_receipts", payload)
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
    async def update_expense_status(
        self,
        expense_id: str,
        new_status: str,
        reimbursement_date: Optional[str] = None
    ) -> dict:
        """Update expense status."""
        params = {
            "expense_id": expense_id,
            "new_status": new_status
        }
        if reimbursement_date:
            params["reimbursement_date"] = reimbursement_date
        
        result = await self.call_tool("update_expense_status", params)
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
    async def get_unreimbursed_balance(self) -> dict:
        """Get unreimbursed balance."""
        result = await self.call_tool("get_unreimbursed_balance", {})
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)

    async def add_numbers(self, a: float, b: float) -> dict:
        """Call test addition MCP tool."""
        result = await self.call_tool("add_numbers", {"a": a, "b": b})
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
    async def stop(self):
        """Stop the MCP server process."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except Exception:
                pass
            self._session = None
        if self._stdio_cm:
            try:
                await self._stdio_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._stdio_cm = None
        self._session_started = False
