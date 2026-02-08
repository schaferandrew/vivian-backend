"""MCP client service for communicating with MCP server."""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Raised when MCP process communication fails."""


class MCPClient:
    """Client for communicating with MCP server via stdio."""
    
    def __init__(self, server_command: list[str], process_env: Optional[dict[str, str]] = None):
        self.server_command = server_command
        self.process: Optional[subprocess.Popen] = None
        self.process_env = process_env
    
    async def start(self):
        """Start the MCP server process."""
        env = self.process_env
        mcp_cwd = os.getcwd()
        if env is None:
            # Resolve latest Google OAuth credentials each time MCP starts.
            from vivian_api.config import Settings
            from vivian_api.services.google_integration import build_mcp_env

            settings = Settings()
            env = build_mcp_env(settings)
            if settings.mcp_server_path:
                candidate = Path(settings.mcp_server_path)
                if candidate.exists():
                    mcp_cwd = str(candidate)
                else:
                    logger.warning(
                        "Configured MCP server path does not exist: %s. Falling back to cwd=%s",
                        settings.mcp_server_path,
                        mcp_cwd,
                    )

        self.process = subprocess.Popen(
            self.server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            cwd=mcp_cwd,
        )
        # TODO: Initialize MCP session
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            },
            "id": 1
        }

        if not self.process:
            raise MCPClientError("MCP server not started")
        if not self.process.stdin or not self.process.stdout:
            raise MCPClientError("MCP server pipes are unavailable")

        try:
            request_line = json.dumps(request) + "\n"
            self.process.stdin.write(request_line)
            self.process.stdin.flush()
        except Exception as exc:
            logger.exception("Failed writing MCP request for tool '%s'", tool_name)
            raise MCPClientError(f"Failed to send request to MCP server: {exc}") from exc

        response_line = self.process.stdout.readline()
        if not response_line:
            stderr_output = ""
            if self.process.stderr:
                try:
                    stderr_output = self.process.stderr.readline().strip()
                except Exception:
                    stderr_output = ""

            if self.process.poll() is not None:
                message = "MCP server exited unexpectedly"
                if stderr_output:
                    message = f"{message}: {stderr_output}"
                logger.error(
                    "MCP process exited during tool '%s' call. stderr=%s",
                    tool_name,
                    stderr_output or "<empty>",
                )
                raise MCPClientError(message)

            message = "MCP server returned an empty response"
            if stderr_output:
                message = f"{message}: {stderr_output}"
            logger.error(
                "MCP returned empty response for tool '%s'. stderr=%s",
                tool_name,
                stderr_output or "<empty>",
            )
            raise MCPClientError(message)

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raw = response_line.strip()
            preview = raw[:220] + ("..." if len(raw) > 220 else "")
            logger.error(
                "Invalid JSON from MCP for tool '%s': %s",
                tool_name,
                preview,
            )
            raise MCPClientError(f"MCP returned invalid JSON response: {preview}") from exc

        if "error" in response:
            logger.error(
                "MCP tool '%s' returned error payload: %s",
                tool_name,
                response.get("error"),
            )
            raise MCPClientError(f"MCP error: {response['error']}")

        return response.get("result", {})

    @staticmethod
    def _parse_tool_json(result: dict) -> dict:
        """Extract JSON payload from MCP tool result content."""
        content = result.get("content")
        if not isinstance(content, list) or not content:
            logger.error("MCP tool returned no content payload")
            raise MCPClientError("MCP tool returned no content")

        text = content[0].get("text")
        if not isinstance(text, str) or not text.strip():
            logger.error("MCP tool returned empty text payload")
            raise MCPClientError("MCP tool returned empty payload")

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            preview = text[:220] + ("..." if len(text) > 220 else "")
            logger.error("MCP tool returned invalid JSON payload: %s", preview)
            raise MCPClientError(f"MCP tool returned invalid JSON payload: {preview}") from exc
    
    async def upload_receipt_to_drive(
        self, 
        local_file_path: str, 
        status: str,
        filename: Optional[str] = None
    ) -> dict:
        """Upload receipt to Google Drive."""
        result = await self.call_tool("upload_receipt_to_drive", {
            "local_file_path": local_file_path,
            "status": status,
            "filename": filename
        })

        return self._parse_tool_json(result)
    
    async def append_to_ledger(
        self,
        expense_json: dict,
        status: str,
        drive_file_id: str
    ) -> dict:
        """Append expense to ledger."""
        result = await self.call_tool("append_expense_to_ledger", {
            "expense_json": expense_json,
            "reimbursement_status": status,
            "drive_file_id": drive_file_id
        })

        return self._parse_tool_json(result)
    
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
        return self._parse_tool_json(result)
    
    async def get_unreimbursed_balance(self) -> dict:
        """Get unreimbursed balance."""
        result = await self.call_tool("get_unreimbursed_balance", {})
        return self._parse_tool_json(result)
    
    async def stop(self):
        """Stop the MCP server process."""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.warning("MCP process did not terminate gracefully; forcing kill")
                self.process.kill()
                self.process.wait(timeout=3)
            finally:
                self.process = None
