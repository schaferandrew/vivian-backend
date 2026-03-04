"""MCP client service for communicating with MCP server."""

from __future__ import annotations

import json
from contextlib import AbstractAsyncContextManager
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import TextContent


class MCPClientError(Exception):
    """Raised when MCP communication fails."""


def _parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a JSON object string."""
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_tool_result_text(result: dict[str, Any]) -> str:
    """Extract text payload from an MCP call_tool response."""
    content = result.get("content")
    if not isinstance(content, list) or not content:
        return "{}"

    first = content[0]
    if not isinstance(first, dict):
        return "{}"

    text = first.get("text")
    if isinstance(text, str):
        return text
    return str(text) if text is not None else "{}"


def extract_tool_result_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract structured payload from MCP response with text JSON fallback."""
    structured = result.get("structured_content")
    if structured is None:
        structured = result.get("structuredContent")

    if hasattr(structured, "model_dump"):
        structured = structured.model_dump()  # type: ignore[assignment]

    if isinstance(structured, dict):
        return structured

    if isinstance(structured, str):
        parsed = _parse_json_object(structured)
        if parsed is not None:
            return parsed

    return _parse_json_object(extract_tool_result_text(result))


class MCPClient:
    """Client for communicating with MCP server via stdio."""

    def __init__(
        self,
        server_command: list[str],
        process_env: Optional[dict[str, str]] = None,
        server_path_override: Optional[str] = None,
        mcp_server_id: Optional[str] = None,
    ):
        self.server_command = server_command
        self.process_env = process_env
        self.server_path_override = server_path_override
        self.mcp_server_id = mcp_server_id
        self._session: Optional[ClientSession] = None
        self._stdio_cm: Optional[AbstractAsyncContextManager] = None
        self._session_started = False

    @classmethod
    async def from_db(
        cls,
        server_command: list[str],
        home_id: str,
        mcp_server_id: str,
        db: "Session",
        server_path_override: Optional[str] = None,
    ) -> "MCPClient":
        """Create an MCPClient with database-backed configuration.

        This factory method loads Google connection tokens and MCP settings
        from the database and builds the environment for the subprocess.

        Args:
            server_command: The MCP server command to run
            home_id: The home ID to load configuration for
            mcp_server_id: The MCP server ID to load settings for
            db: Database session
            server_path_override: Optional path override for server CWD

        Returns:
            Configured MCPClient instance
        """
        from vivian_api.config import Settings
        from vivian_api.services.google_integration import build_mcp_env_from_db

        settings = Settings()
        env = await build_mcp_env_from_db(home_id, mcp_server_id, db, settings)

        return cls(
            server_command=server_command,
            process_env=env,
            server_path_override=server_path_override,
            mcp_server_id=mcp_server_id,
        )

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

    def _parse_tool_payload(self, result: dict[str, Any]) -> dict[str, Any]:
        """Return normalized tool payload with empty-object fallback."""
        payload = extract_tool_result_payload(result)
        if isinstance(payload, dict):
            return payload
        return {}

    async def call_tool(self, tool_name: str, arguments: dict) -> dict[str, Any]:
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
                for part in getattr(result, "content", []):
                    if isinstance(part, TextContent):
                        content.append({"type": "text", "text": part.text})
                        continue

                    text = getattr(part, "text", None)
                    if text is not None:
                        content.append({"type": "text", "text": str(text)})

                structured_content = getattr(result, "structuredContent", None)
                normalized_result = {
                    "content": content,
                    "structured_content": structured_content,
                }

                if getattr(result, "isError", False):
                    payload = extract_tool_result_payload(normalized_result)
                    if isinstance(payload, dict) and payload.get("error"):
                        error_text = str(payload.get("error"))
                    else:
                        error_text = extract_tool_result_text(normalized_result)
                    raise MCPClientError(f"MCP tool '{tool_name}' returned error: {error_text}")

                return normalized_result
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
        return self._parse_tool_payload(result)

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
        return self._parse_tool_payload(result)

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
        if donation_year is not None:
            payload["tax_year"] = str(donation_year)
        if filename:
            payload["filename"] = filename

        result = await self.call_tool("upload_charitable_receipt_to_drive", payload)
        return self._parse_tool_payload(result)

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
        return self._parse_tool_payload(result)

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
        return self._parse_tool_payload(result)

    async def check_charitable_duplicates(
        self,
        donation_json: dict,
        fuzzy_days: int = 3
    ) -> dict:
        """Check for duplicate charitable donations in the ledger.

        Args:
            donation_json: Donation data with organization_name, donation_date, amount
            fuzzy_days: Number of days to allow for fuzzy date matching

        Returns:
            Dict with is_duplicate, potential_duplicates, recommendation
        """
        payload = {"donation_json": donation_json}
        if fuzzy_days != 3:
            payload["fuzzy_days"] = fuzzy_days
        result = await self.call_tool("check_charitable_duplicates", payload)
        return self._parse_tool_payload(result)

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
        return self._parse_tool_payload(result)

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
        return self._parse_tool_payload(result)

    async def get_unreimbursed_balance(self) -> dict:
        """Get unreimbursed balance."""
        result = await self.call_tool("get_unreimbursed_balance", {})
        return self._parse_tool_payload(result)

    async def read_ledger_entries(
        self,
        year: int | None = None,
        status_filter: str | None = None,
        limit: int = 1000,
        column_filters: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Read HSA ledger entries with optional filters."""
        payload: dict[str, Any] = {"limit": limit}
        if year is not None:
            payload["year"] = year
        if status_filter:
            payload["status_filter"] = status_filter
        if column_filters:
            payload["column_filters"] = column_filters
        result = await self.call_tool("read_ledger_entries", payload)
        return self._parse_tool_payload(result)

    async def get_charitable_summary(
        self,
        tax_year: str | None = None,
        column_filters: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Get charitable summary with optional filters."""
        payload: dict[str, Any] = {}
        if tax_year:
            payload["tax_year"] = tax_year
        if column_filters:
            payload["column_filters"] = column_filters
        result = await self.call_tool("get_charitable_summary", payload)
        return self._parse_tool_payload(result)

    async def read_charitable_ledger_entries(
        self,
        tax_year: str | int | None = None,
        organization: str | None = None,
        tax_deductible: bool | None = None,
        limit: int = 1000,
        column_filters: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Read charitable ledger entries with optional filters."""
        payload: dict[str, Any] = {"limit": limit}
        if tax_year is not None and str(tax_year).strip():
            payload["tax_year"] = str(tax_year).strip()
        if organization:
            payload["organization"] = organization
        if isinstance(tax_deductible, bool):
            payload["tax_deductible"] = tax_deductible
        if column_filters:
            payload["column_filters"] = column_filters
        result = await self.call_tool("read_charitable_ledger_entries", payload)
        return self._parse_tool_payload(result)

    async def add_numbers(self, a: float, b: float) -> dict:
        """Call test addition MCP tool."""
        result = await self.call_tool("add_numbers", {"a": a, "b": b})
        return self._parse_tool_payload(result)

    async def stop(self):
        """Stop the MCP server process."""
        if self._session:
            try:
                await self._session.__aexit__(None, None, None)
            except BaseException:
                pass
            self._session = None
        if self._stdio_cm:
            try:
                await self._stdio_cm.__aexit__(None, None, None)
            except BaseException:
                pass
            self._stdio_cm = None
        self._session_started = False
