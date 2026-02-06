"""MCP client service for communicating with MCP server."""

import asyncio
import json
import subprocess
from typing import Optional


class MCPClient:
    """Client for communicating with MCP server via stdio."""
    
    def __init__(self, server_command: list[str]):
        self.server_command = server_command
        self.process: Optional[subprocess.Popen] = None
    
    async def start(self):
        """Start the MCP server process."""
        self.process = subprocess.Popen(
            self.server_command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        # TODO: Initialize MCP session
    
    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on the MCP server."""
        # This is a simplified version - real MCP uses JSON-RPC over stdio
        # For now, we'll simulate with subprocess calls
        
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
            raise RuntimeError("MCP server not started")
        
        # Send request
        request_line = json.dumps(request) + "\n"
        self.process.stdin.write(request_line)
        self.process.stdin.flush()
        
        # Read response
        response_line = self.process.stdout.readline()
        response = json.loads(response_line)
        
        if "error" in response:
            raise Exception(f"MCP error: {response['error']}")
        
        return response.get("result", {})
    
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
        
        # Parse the text content from MCP response
        content = result.get("content", [{}])[0].get("text", "{}")
        return json.loads(content)
    
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
    
    async def stop(self):
        """Stop the MCP server process."""
        if self.process:
            self.process.terminate()
            self.process.wait()
