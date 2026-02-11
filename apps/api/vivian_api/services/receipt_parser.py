"""OpenRouter service for receipt parsing."""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from vivian_api.config import Settings
from vivian_api.utils import validate_temp_file_path, InvalidFilePathError


logger = logging.getLogger(__name__)
RECEIPT_PARSING_PROMPT = """You are a receipt parsing assistant. Extract the following information from this medical receipt:

1. Provider: Medical provider or facility name
2. Service Date: When the service was provided (YYYY-MM-DD format)
3. Paid Date: When payment was made (YYYY-MM-DD format, often same as service date)
4. Amount: Total amount paid (numeric only, no $ sign)
5. HSA Eligible: Whether this appears to be an HSA-eligible medical expense (true/false)

Return ONLY a JSON object in this exact format:
{
    "provider": "Provider Name",
    "service_date": "2024-01-15",
    "paid_date": "2024-01-15",
    "amount": 125.00,
    "hsa_eligible": true
}

If any field is unclear or missing, use null for dates and 0 for amount.
Be precise with dates - look for service date vs payment date carefully.
For HSA eligibility: medical services, prescriptions, and doctor visits are typically eligible. Non-medical items like parking, food, or retail are not eligible."""


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
        # Validate file path to prevent path traversal attacks
        try:
            validated_path = validate_temp_file_path(
                pdf_path,
                self.settings.temp_upload_dir
            )
        except (InvalidFilePathError, FileNotFoundError) as exc:
            logger.warning(
                "File validation failed in receipt parser",
                extra={"error_type": type(exc).__name__}
            )
            return {
                "success": False,
                "error": "Invalid or inaccessible file. Please ensure the file was uploaded correctly.",
            }
        
        # Read PDF and encode as base64
        with open(validated_path, "rb") as f:
            pdf_content = f.read()
            pdf_base64 = base64.b64encode(pdf_content).decode("utf-8")
        
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
            
            return {
                "success": True,
                "parsed_data": parsed,
                "raw_output": content,
                "model": data.get("model", self.settings.openrouter_model)
            }
            
        except json.JSONDecodeError as e:
            return {
                "success": False,
                "error": f"Failed to parse JSON from model output: {str(e)}",
                "raw_output": content
            }
    
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
