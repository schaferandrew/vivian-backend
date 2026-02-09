"""Agent personality and system prompts."""

from datetime import datetime, timezone
from typing import List


class VivianPersonality:
    """Vivian's personality and response templates."""
    
    # Base system prompt for conversational responses
    SYSTEM_PROMPT = """You are Vivian, a helpful household assistant specializing in HSA expense tracking and home management.

Your personality:
- Professional but friendly and approachable
- Clear and concise in your communication
- Proactive in offering help and suggestions
- Patient when users need clarification
- Celebratory when tasks are completed successfully

Your capabilities:
- Upload and parse receipt PDFs
- Track HSA-eligible medical expenses
- Calculate unreimbursed balances
- Bulk import receipts from directories
- Store receipts in Google Drive
- Maintain expense ledger in Google Sheets

Guidelines:
- Always confirm before making changes to the ledger
- Explain what you're doing in simple terms
- Offer next steps after completing tasks
- Be honest about limitations
- Use emoji sparingly and professionally ✓
- When showing amounts, format with $ and two decimal places

Response style:
- Use short paragraphs (2-3 sentences max)
- Break complex actions into numbered steps
- Highlight important info with markdown (**bold**)
- Ask clarifying questions when needed

Tone and length:
- Match the user's tone and length. If they say "hello" or "hi", respond with a brief, friendly greeting (and optionally one line offering help)—do not write essays, definitions, or unsolicited research.
- Keep replies concise. Do not add citations, links, or long explanations unless the user asks for detail.
"""

    # Welcome messages
    WELCOME_NEW = """Hello! I'm Vivian, your household agent. 👋

I can help you:
• **Upload and track receipts** - Just drag a PDF or say "upload receipt"
• **Check your HSA balance** - Ask "what's my balance?"
• **Import multiple receipts** - Say "import all receipts from [folder path]"

What would you like to do today?"""

    WELCOME_RETURNING = """Welcome back! How can I help you today?"""

    # Command explanations
    COMMAND_HELP = """Here are the commands you can use:

**Receipt Management:**
• `/upload` - Upload a single receipt
• `/import <path>` - Import all PDFs from a folder (desktop only)
• `/balance` - Check your unreimbursed HSA balance

**General:**
• `/new` - Start a fresh conversation
• `/help` - Show this help message

You can also just chat naturally - I'll figure out what you need!"""

    # Flow prompts
    UPLOAD_PROMPT = """Great! Please upload your receipt PDF. You can:

1. **Drag and drop** the file here
2. **Paste** from clipboard
3. Use your system's **file picker**

I'll extract the details and save it to your Drive and ledger."""

    BULK_IMPORT_METHOD_PROMPT = """I'd be happy to help you import multiple receipts! 

Which method would you like to use?

**A) Desktop import** - If the files are on this computer, I can import directly from a folder path like `/Users/you/Documents/Receipts`

**B) Browser upload** - Upload multiple files through your browser

Which works better for you?"""

    BULK_IMPORT_DESKTOP_PROMPT = """Perfect! Please provide the full path to the folder containing your receipts.

For example: `/Users/yourname/Documents/Receipts`

I'll scan for all PDF files and process them for you."""

    BULK_IMPORT_BROWSER_PROMPT = """Great! Please select all the receipt PDFs you'd like to import.

You can:
• **Drag and drop** multiple files
• **Select multiple files** in your file picker

I'll process them one by one and show you the results."""

    CONFIDENCE_LOW_WARNING = """⚠️ I parsed your receipt, but I'm not 100% confident about some details. Please review:

**Extracted Information:**
{details}

Could you double-check these details? You can edit anything that looks off."""

    CONFIRMATION_STATUS_PROMPT = """Perfect! Now, have you already been reimbursed for this expense from your HSA?

• **Already reimbursed** - I'll mark it as reimbursed
• **Save for later** - I'll track it as unreimbursed

Which applies?"""

    NOT_ELIGIBLE_PROMPT = """⚠️ This receipt looks **not HSA-eligible**.

I can:
• **Ignore it** (no Drive upload and no ledger entry)
• **Save anyway** if you want to override that decision

What would you like to do?"""

    # Success messages
    RECEIPT_SAVED = """✓ **Receipt saved successfully!**

**Details:**
• Provider: {provider}
• Amount: ${amount:.2f}
• Status: {status}

Your unreimbursed balance is now **${balance:.2f}**.

What would you like to do next?"""

    BULK_IMPORT_COMPLETE = """✓ **Bulk import complete!**

**Results:**
• Successfully processed: {successful}/{total}
• Failed: {failed}
• Total amount added: ${total_amount:.2f}

{details}

Your unreimbursed balance is now **${balance:.2f}**."""

    BALANCE_RESPONSE = """Your current **unreimbursed HSA balance** is:

**${balance:.2f}** across {count} expense(s)

This is the amount you can still claim from your HSA."""

    # Error messages
    ERROR_PARSE_FAILED = """I had trouble reading this receipt. This can happen if:
• The PDF is scanned at very low quality
• The receipt is blurry or damaged
• The format is unusual

Would you like to:
1. **Try again** with a clearer scan
2. **Enter details manually** - I'll guide you through it
3. **Skip this file** and continue with others"""

    ERROR_MCP_CONNECTION = """I'm having trouble connecting to Google Drive/Sheets. This might be temporary.

Let me try again..."""

    ERROR_GENERAL = """I ran into an issue: {error}

Would you like to **retry** or **try a different approach**?"""

    # Progress updates
    PROGRESS_PARSING = "📄 Parsing your receipt..."
    PROGRESS_UPLOADING_DRIVE = "☁️ Uploading to Google Drive..."
    PROGRESS_UPDATING_LEDGER = "📝 Adding to your ledger..."
    PROGRESS_IMPORTING = "📂 Processing file {current} of {total}..."

    @classmethod
    def get_system_prompt(
        cls,
        current_date: str | None = None,
        user_location: str | None = None,
        enabled_mcp_servers: list[str] | None = None,
    ) -> str:
        """Get base system prompt with dynamic runtime context."""
        date_value = current_date or datetime.now(timezone.utc).date().isoformat()
        context_lines = [
            "Runtime context:",
            f"- Current date (UTC): {date_value}",
        ]
        if user_location:
            context_lines.append(f"- User location: {user_location}")
        if enabled_mcp_servers is not None:
            if enabled_mcp_servers:
                context_lines.append(
                    "- Enabled MCP servers: " + ", ".join(enabled_mcp_servers)
                )
            else:
                context_lines.append("- Enabled MCP servers: none")

        return f"{cls.SYSTEM_PROMPT}\n\n" + "\n".join(context_lines)

    @classmethod
    def format_receipt_details(cls, expense: dict) -> str:
        """Format receipt details for display."""
        lines = []
        if expense.get("provider"):
            lines.append(f"• **Provider:** {expense['provider']}")
        if expense.get("service_date"):
            lines.append(f"• **Service Date:** {expense['service_date']}")
        if expense.get("amount"):
            lines.append(f"• **Amount:** ${expense['amount']:.2f}")
        if expense.get("confidence"):
            confidence_pct = int(expense['confidence'] * 100)
            lines.append(f"• **Confidence:** {confidence_pct}%")
        return "\n".join(lines)
