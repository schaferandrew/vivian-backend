"""Balance query flow handler."""

from vivian_api.chat.session import ChatSession, FlowType
from vivian_api.chat.connection import connection_manager
from vivian_api.chat.personality import VivianPersonality
from vivian_api.services.mcp_client import MCPClient


class BalanceFlow:
    """Handles balance query flow."""
    
    async def execute(self, session: ChatSession):
        """Execute balance query."""
        # Start flow (brief, no multi-turn needed)
        session.start_flow(FlowType.BALANCE)
        
        await connection_manager.send_typing(session, True)
        
        try:
            # Get balance from MCP
            mcp_client = MCPClient(["python", "-m", "vivian_mcp.server"])
            await mcp_client.start()
            
            result = await mcp_client.get_unreimbursed_balance()
            await mcp_client.stop()
            
            balance = result.get("total_unreimbursed", 0)
            count = result.get("count", 0)
            
            # Update session context
            session.context.last_balance_query = __import__('datetime').datetime.utcnow()
            session.context.last_balance_result = result
            
            # Send response
            message = VivianPersonality.BALANCE_RESPONSE.format(
                balance=balance,
                count=count
            )
            
            await connection_manager.send_text(session, message, add_to_history=True)
            
            # Offer additional actions
            await connection_manager.send_confirmation(
                session,
                prompt_id=f"balance_actions_{session.session_id}",
                message="Would you like to see more details?",
                actions=[
                    {"id": "view_details", "label": "View expense details", "style": "secondary"},
                    {"id": "upload_receipt", "label": "Upload a receipt", "style": "primary"},
                    {"id": "no_thanks", "label": "No thanks", "style": "secondary"}
                ]
            )
            
            session.end_flow()
            
        except Exception as e:
            await connection_manager.send_error(
                session,
                error_id=f"balance_error_{session.session_id}",
                category="mcp_error",
                severity="external",
                message=f"I had trouble fetching your balance: {str(e)}\n\n{VivianPersonality.ERROR_MCP_CONNECTION}",
                recovery_options=[
                    {"id": "retry", "label": "Try again"}
                ]
            )
