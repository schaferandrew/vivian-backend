"""MCP client service for communicating with MCP server."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    """Raised when MCP process communication fails."""


class MCPClient:
    """Client for communicating with MCP server via stdio."""
    
    def __init__(
        self,
        server_command: list[str],
        process_env: Optional[dict[str, str]] = None,
        server_path_override: Optional[str] = None,
    ):
        self.server_command = server_command
        self.process: Optional[subprocess.Popen] = None
        self.process_env = process_env
        self.server_path_override = server_path_override
        self.startup_error: Optional[str] = None
        self._initialized = False
        self._request_id = 0

    @staticmethod
    def _is_python_command(command_part: str) -> bool:
        """Return True when the executable looks like a Python interpreter."""
        if not command_part:
            return False
        executable = Path(command_part).name.lower()
        return executable.startswith("python")

    @staticmethod
    def _resolve_mcp_cwd(configured_path: str) -> tuple[Optional[str], list[str]]:
        """Resolve MCP server working directory across docker and local dev."""
        candidate_paths: list[Path] = []
        if configured_path:
            candidate_paths.append(Path(configured_path))

        # Local fallback: apps/mcp-server (sibling of apps/api).
        local_candidate = Path(__file__).resolve().parents[3] / "mcp-server"
        if local_candidate not in candidate_paths:
            candidate_paths.append(local_candidate)

        checked_paths: list[str] = []
        for candidate in candidate_paths:
            checked_paths.append(str(candidate))
            if candidate.exists() and candidate.is_dir():
                return str(candidate), checked_paths

        return None, checked_paths

    @staticmethod
    def _resolve_server_command(server_command: list[str], mcp_cwd: str) -> list[str]:
        """Resolve executable for MCP server process."""
        command = list(server_command)
        if not command:
            return [sys.executable, "-m", "vivian_mcp.server"]

        if MCPClient._is_python_command(command[0]):
            venv_candidates = [
                Path(mcp_cwd) / "venv" / "bin" / "python",
                Path(mcp_cwd) / ".venv" / "bin" / "python",
                Path(mcp_cwd) / "venv" / "Scripts" / "python.exe",
                Path(mcp_cwd) / ".venv" / "Scripts" / "python.exe",
            ]
            for candidate in venv_candidates:
                if candidate.exists():
                    command[0] = str(candidate)
                    break
            else:
                command[0] = sys.executable

        return command

    def _next_request_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id

    def _read_response_for_id(self, expected_id: int, context: str) -> dict:
        """Read stdout lines until matching JSON-RPC response ID is found."""
        if not self.process or not self.process.stdout:
            raise MCPClientError("MCP server pipes are unavailable")

        while True:
            response_line = self.process.stdout.readline()
            if not response_line:
                stderr_output = ""
                if self.process.stderr:
                    try:
                        if self.process.poll() is not None:
                            stderr_output = (self.process.stderr.read() or "").strip()
                        else:
                            stderr_output = (self.process.stderr.readline() or "").strip()
                    except Exception:
                        stderr_output = ""
                stderr_preview = stderr_output.splitlines()[-1] if stderr_output else ""

                if self.process.poll() is not None:
                    message = f"MCP server exited unexpectedly during {context}"
                    if stderr_preview:
                        message = f"{message}: {stderr_preview}"
                    raise MCPClientError(message)

                message = f"MCP server returned an empty response during {context}"
                if stderr_preview:
                    message = f"{message}: {stderr_preview}"
                raise MCPClientError(message)

            raw = response_line.strip()
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("Ignoring non-JSON MCP stdout line while waiting for id=%s: %s", expected_id, raw[:220])
                continue

            if response.get("id") == expected_id:
                return response

            logger.debug(
                "Ignoring out-of-band MCP message while waiting for id=%s: id=%s method=%s",
                expected_id,
                response.get("id"),
                response.get("method"),
            )

    def _send_request(self, method: str, params: dict, context: str) -> dict:
        """Send a JSON-RPC request and return matching response."""
        if not self.process or not self.process.stdin:
            raise MCPClientError("MCP server not started")

        request_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise MCPClientError(f"Failed to send request to MCP server: {exc}") from exc

        return self._read_response_for_id(request_id, context)

    def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            raise MCPClientError("MCP server not started")

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self.process.stdin.write(json.dumps(notification) + "\n")
        self.process.stdin.flush()

    def _initialize_session(self) -> None:
        """Perform MCP initialize handshake."""
        init_params_variants = [
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "vivian-api", "version": "0.1.0"},
            },
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "vivian-api", "version": "0.1.0"},
            },
        ]
        last_error: Optional[dict] = None

        for params in init_params_variants:
            response = self._send_request("initialize", params, "initialize handshake")
            if "error" in response:
                last_error = response["error"]
                continue

            try:
                self._send_notification("notifications/initialized", {})
            except Exception as exc:
                raise MCPClientError(f"MCP initialize notification failed: {exc}") from exc

            self._initialized = True
            logger.info("MCP initialized successfully with protocolVersion=%s", params["protocolVersion"])
            return

        raise MCPClientError(f"MCP initialize failed: {last_error}")
    
    async def start(self):
        """Start the MCP server process."""
        self.startup_error = None
        self._initialized = False
        self._request_id = 0
        from vivian_api.config import Settings

        settings = Settings()
        configured_path = self.server_path_override or settings.mcp_server_path
        mcp_cwd, checked_paths = self._resolve_mcp_cwd(configured_path)
        if not mcp_cwd:
            self.startup_error = (
                "MCP server directory not found. "
                f"Configured path: {configured_path!r}. "
                f"Checked paths: {checked_paths}. "
                "Rebuild the API image and verify configured MCP server paths."
            )
            logger.error(
                "MCP server directory not found. configured_path=%s checked_paths=%s cwd=%s",
                configured_path,
                checked_paths,
                os.getcwd(),
            )
            return

        env = dict(self.process_env) if self.process_env is not None else None
        if env is None:
            # Resolve latest Google OAuth credentials each time MCP starts.
            from vivian_api.services.google_integration import build_mcp_env

            env = build_mcp_env(settings)

        # Ensure local source import works even when vivian_mcp is not pip-installed.
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{mcp_cwd}{os.pathsep}{existing_pythonpath}"
            if existing_pythonpath
            else mcp_cwd
        )

        command = self._resolve_server_command(self.server_command, mcp_cwd)
        logger.info("Launching MCP subprocess. command=%s cwd=%s", command, mcp_cwd)

        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
                cwd=mcp_cwd,
            )
        except OSError as exc:
            self.process = None
            self.startup_error = f"Failed to start MCP server process: {exc}"
            logger.exception(
                "MCP process launch failed. command=%s cwd=%s",
                command,
                mcp_cwd,
            )
            return

        if self.process.poll() is not None:
            stderr_output = ""
            if self.process.stderr:
                stderr_output = (self.process.stderr.read() or "").strip()
            stderr_preview = stderr_output.splitlines()[-1] if stderr_output else ""
            detail = f": {stderr_preview}" if stderr_preview else ""
            self.startup_error = f"MCP server exited during startup{detail}"
            logger.error(
                "MCP process exited immediately. command=%s cwd=%s rc=%s stderr=%s",
                command,
                mcp_cwd,
                self.process.returncode,
                stderr_preview or "<empty>",
            )
            self.process = None
            return

        try:
            self._initialize_session()
        except MCPClientError as exc:
            self.startup_error = str(exc)
            logger.error("MCP initialize failed. command=%s cwd=%s error=%s", command, mcp_cwd, exc)
            if self.process:
                self.process.terminate()
                self.process = None
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        if self.startup_error:
            raise MCPClientError(self.startup_error)
        if not self.process:
            raise MCPClientError("MCP server not started")
        if not self._initialized:
            raise MCPClientError("MCP server not initialized")

        try:
            response = self._send_request(
                "tools/call",
                {
                    "name": tool_name,
                    "arguments": arguments,
                },
                f"tool '{tool_name}' call",
            )
        except MCPClientError:
            raise
        except Exception as exc:
            logger.exception("Failed writing MCP request for tool '%s'", tool_name)
            raise MCPClientError(f"Failed to send request to MCP server: {exc}") from exc

        if "error" in response:
            logger.error(
                "MCP tool '%s' returned error payload: %s arguments=%s",
                tool_name,
                response.get("error"),
                arguments,
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
        args = {
            "local_file_path": local_file_path,
            "status": status,
        }
        if filename is not None:
            args["filename"] = filename

        result = await self.call_tool("upload_receipt_to_drive", args)

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

    async def add_numbers(self, a: float, b: float) -> dict:
        """Call test MCP addition tool."""
        result = await self.call_tool("add_numbers", {"a": a, "b": b})
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
                self.startup_error = None
                self._initialized = False
