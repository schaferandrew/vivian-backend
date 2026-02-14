"""OpenRouter service for receipt parsing."""

import base64
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from vivian_api.config import Settings
from vivian_api.utils import validate_temp_file_path, InvalidFilePathError
from vivian_api.logging_service import log_with_context


logger = logging.getLogger(__name__)
RECEIPT_PARSING_PROMPT = """You are a receipt parsing assistant. Determine whether this receipt is a medical/HSA expense or a charitable donation, then extract the correct fields.

First, decide the category:
- "hsa" for medical receipts, prescriptions, doctor visits, or other HSA-eligible expenses
- "charitable" for donations to organizations, churches, nonprofits, etc.

Return ONLY a JSON object in this exact format:
{
    "category": "hsa" | "charitable",
    "provider": "Provider Name",
    "service_date": "YYYY-MM-DD",
    "paid_date": "YYYY-MM-DD",
    "amount": 125.00,
    "hsa_eligible": true,
    "organization_name": "Organization Name",
    "donation_date": "YYYY-MM-DD",
    "tax_deductible": true,
    "description": "Optional short note"
}

Rules:
- For HSA receipts, fill provider/service_date/paid_date/amount/hsa_eligible and leave charitable fields empty or null.
- For charitable receipts, fill organization_name/donation_date/amount/tax_deductible/description and leave HSA fields empty or null.
- If any field is unclear or missing, use null for dates and 0 for amount.
- Be precise with dates and amounts.
"""


class OpenRouterService:
    """Service for interacting with OpenRouter API."""
    
    def __init__(self):
        self.settings = Settings()
        self.client = httpx.AsyncClient(
            base_url=self.settings.openrouter_base_url,
            headers={
                "Authorization": f"Bearer {self.settings.openrouter_api_key}",
                "HTTP-Referer": "http://localhost:8000",
                "X-Title": "Vivian Household Agent"
            },
            timeout=60.0
        )
    
    async def parse_receipt(self, pdf_path: str) -> dict:
        """Parse a receipt PDF using OpenRouter vision model."""
        start_time = time.time()
        logger.debug(f"Starting receipt parsing for {pdf_path}")
        
        # Validate file path to prevent path traversal attacks
        try:
            validated_path = validate_temp_file_path(
                pdf_path,
                self.settings.temp_upload_dir
            )
        except (InvalidFilePathError, FileNotFoundError) as exc:
            log_with_context(
                logger,
                "WARNING",
                "File validation failed",
                service="receipt_parser",
                error_type=type(exc).__name__,
                file_path=pdf_path,
            )
            return {
                "success": False,
                "error": "Invalid or inaccessible file. Please ensure the file was uploaded correctly.",
            }
        
        # Read PDF and encode as base64
        with open(validated_path, "rb") as f:
            pdf_content = f.read()
            pdf_base64 = base64.b64encode(pdf_content).decode("utf-8")
        
        log_with_context(
            logger,
            "DEBUG",
            "PDF loaded and encoded",
            service="receipt_parser",
            file_size_bytes=len(pdf_content),
            encoded_size_bytes=len(pdf_base64),
        )
        
        # Prepare message with PDF as image (OpenRouter supports PDF as base64 image)
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": RECEIPT_PARSING_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:application/pdf;base64,{pdf_base64}"
                        }
                    }
                ]
            }
        ]
        
        # Call OpenRouter
        api_start = time.time()
        log_with_context(
            logger,
            "DEBUG",
            "Calling OpenRouter API",
            service="receipt_parser",
            model=self.settings.openrouter_model,
        )
        
        try:
            response = await self.client.post(
                "/chat/completions",
                json={
                    "model": self.settings.openrouter_model,
                    "messages": messages,
                    "max_tokens": 1000,
                    "temperature": 0.1,
                    "plugins": [{"id": "web", "enabled": False}],  # Explicitly disable web search to avoid unexpected charges
                }
            )
            
            response.raise_for_status()
            data = response.json()
            
            api_duration = time.time() - api_start
            log_with_context(
                logger,
                "DEBUG",
                "OpenRouter API call completed",
                service="receipt_parser",
                duration_ms=round(api_duration * 1000, 2),
                status_code=response.status_code,
            )
        except httpx.HTTPError as e:
            api_duration = time.time() - api_start
            log_with_context(
                logger,
                "ERROR",
                "OpenRouter API call failed",
                service="receipt_parser",
                error=str(e),
                duration_ms=round(api_duration * 1000, 2),
            )
            raise
        
        # Extract content from response
        content = data["choices"][0]["message"]["content"]
        
        # Parse JSON from response
        try:
            # Try to find JSON in the response (model might wrap it in markdown)
            json_start = content.find("{")
            json_end = content.rfind("}")
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end + 1]
                parsed = json.loads(json_str)
            else:
                parsed = json.loads(content)
            
            total_duration = time.time() - start_time
            log_with_context(
                logger,
                "INFO",
                "Receipt parsed successfully",
                service="receipt_parser",
                duration_ms=round(total_duration * 1000, 2),
                category=parsed.get("category", "unknown"),
            )
            
            return {
                "success": True,
                "parsed_data": parsed,
                "raw_output": content,
                "model": data.get("model", self.settings.openrouter_model)
            }
            
        except json.JSONDecodeError as e:
            log_with_context(
                logger,
                "ERROR",
                "Failed to parse JSON from model output",
                service="receipt_parser",
                error=str(e),
                duration_ms=round((time.time() - start_time) * 1000, 2),
            )
            return {
                "success": False,
                "error": f"Failed to parse JSON from model output: {str(e)}",
                "raw_output": content
            }
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
