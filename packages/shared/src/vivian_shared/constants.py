"""Shared constants for Vivian household agent."""

from pathlib import Path

# Directory names within the Google Drive root
REIMBURSED_DIR = "reimbursed_receipts"
UNREIMBURSED_DIR = "unreimbursed_receipts"
NOT_ELIGIBLE_DIR = "not_hsa_eligible_receipts"

# Local temp storage
TEMP_UPLOAD_DIR = Path("/tmp/vivian-uploads")

# Confidence threshold for requiring human review
CONFIDENCE_THRESHOLD = 0.85

# OpenRouter model for receipt parsing
DEFAULT_RECEIPT_MODEL = "anthropic/claude-3.5-sonnet-20240620"  # or "google/gemini-flash-1.5"

# Ledger sheet name
LEDGER_SHEET_NAME = "HSA_Ledger"

# API prefixes
API_V1_PREFIX = "/api/v1"
