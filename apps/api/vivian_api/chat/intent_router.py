"""Intent router for classifying user messages."""

import json
import re
from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field

from vivian_api.services.receipt_parser import OpenRouterService


class IntentCategory(str, Enum):
    """Categories of user intent."""
    # Receipt Management
    RECEIPT_UPLOAD = "receipt_upload"
    BULK_IMPORT = "bulk_import"
    RECEIPT_REVIEW = "receipt_review"
    
    # Queries
    BALANCE_QUERY = "balance_query"
    EXPENSE_HISTORY = "expense_history"
    RECEIPT_SEARCH = "receipt_search"
    
    # Actions
    MARK_REIMBURSED = "mark_reimbursed"
    DELETE_RECEIPT = "delete_receipt"
    
    # General
    GREETING = "greeting"
    HELP = "help"
    GOODBYE = "goodbye"
    SMALL_TALK = "small_talk"
    
    # Special
    UNCLEAR = "unclear"
    OUT_OF_SCOPE = "out_of_scope"


class IntentClassification(BaseModel):
    """Result of intent classification."""
    intent: IntentCategory
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_entities: Dict[str, Any] = Field(default_factory=dict)
    clarification_needed: bool = False
    clarification_prompt: Optional[str] = None
    suggested_flow: Optional[str] = None


INTENT_ROUTER_PROMPT = """You are Vivian's Intent Classification System. Analyze user messages and classify intent.

## Available Intents

**Receipt Management:**
- `receipt_upload` - User wants to upload a single receipt
- `bulk_import` - User wants to import multiple receipts
- `receipt_review` - User wants to review/edit existing receipt

**Queries:**
- `balance_query` - User wants HSA balance
- `expense_history` - User wants to see past expenses
- `receipt_search` - User is looking for specific receipt

**Actions:**
- `mark_reimbursed` - User wants to mark expenses as reimbursed
- `delete_receipt` - User wants to remove receipt

**General:**
- `greeting` - Hello, hi, good morning
- `help` - User needs assistance
- `goodbye` - Bye, thanks, see you
- `small_talk` - Casual conversation

**Special:**
- `unclear` - Ambiguous, needs clarification
- `out_of_scope` - Outside Vivian's capabilities

## Rules
1. Prioritize specificity: "upload all receipts" = `bulk_import`
2. Context matters
3. Look for implicit intents
4. Handle uncertainty

## Response Format
Return ONLY a JSON object:
{{
  "intent": "intent_category",
  "confidence": 0.95,
  "extracted_entities": {{
    "directory_path": null,
    "keywords": ["upload", "receipt"]
  }},
  "clarification_needed": false,
  "clarification_prompt": null,
  "suggested_flow": "receipt_upload"
}}

## Examples

User: "Hi there!"
{{"intent": "greeting", "confidence": 0.99, "extracted_entities": {{}}, "clarification_needed": false, "clarification_prompt": null, "suggested_flow": null}}

User: "I need to upload a receipt"
{{"intent": "receipt_upload", "confidence": 0.95, "extracted_entities": {{"keywords": ["upload", "receipt"]}}, "clarification_needed": false, "clarification_prompt": null, "suggested_flow": "receipt_upload"}}

User: "Import all receipts from /Users/me/Docs"
{{"intent": "bulk_import", "confidence": 0.97, "extracted_entities": {{"directory_path": "/Users/me/Docs", "keywords": ["import", "all"]}}, "clarification_needed": false, "clarification_prompt": null, "suggested_flow": "bulk_import"}}

User: "How much is unreimbursed?"
{{"intent": "balance_query", "confidence": 0.98, "extracted_entities": {{"keywords": ["how much", "unreimbursed"]}}, "clarification_needed": false, "clarification_prompt": null, "suggested_flow": "balance_query"}}

Now classify this message:
User: "{user_message}"
"""


class IntentRouter:
    """Routes user messages to appropriate intents."""
    
    def __init__(self):
        self.llm = OpenRouterService()
        self.fallback_patterns = self._compile_patterns()
    
    def _compile_patterns(self) -> Dict[IntentCategory, List[re.Pattern]]:
        """Compile regex patterns for fallback classification."""
        return {
            IntentCategory.RECEIPT_UPLOAD: [
                re.compile(r'\b(upload|add|save).{0,20}(receipt|bill|invoice)', re.I),
                re.compile(r'\b(receipt|bill).{0,20}(upload|add|save)', re.I),
                re.compile(r'\bhave\b.{0,20}\breceipt', re.I),
            ],
            IntentCategory.BULK_IMPORT: [
                re.compile(r'\b(import|process).{0,30}(all|folder|directory|multiple|many)', re.I),
                re.compile(r'\b(bulk|batch|mass).{0,20}(import|upload)', re.I),
                re.compile(r'\ball.{0,20}(receipt|pdf)', re.I),
            ],
            IntentCategory.BALANCE_QUERY: [
                re.compile(r'\b(balance|how much|total|unreimbursed)', re.I),
                re.compile(r'\b(hsa).{0,20}(balance|money|amount)', re.I),
                re.compile(r'\bwaiting.{0,20}(reimburse|reimbursed)', re.I),
            ],
            IntentCategory.GREETING: [
                re.compile(r'^(hi|hello|hey|good morning|good afternoon|good evening)', re.I),
                re.compile(r'\b(hi|hello)\b$', re.I),
            ],
            IntentCategory.HELP: [
                re.compile(r'\b(help|how do|how can|what can|how to)', re.I),
                re.compile(r'\b(what is|who are|what do)', re.I),
            ],
            IntentCategory.GOODBYE: [
                re.compile(r'\b(bye|goodbye|see you|later|thanks|thank you)', re.I),
            ],
        }
    
    async def classify(self, message: str, conversation_history: Optional[List[Dict]] = None) -> IntentClassification:
        """Classify user intent using LLM with fallback to patterns."""
        # Try LLM-based classification first
        try:
            prompt = INTENT_ROUTER_PROMPT.format(user_message=message)
            
            response = await self.llm.client.post(
                "/chat/completions",
                json={
                    "model": self.llm.settings.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.1
                }
            )
            response.raise_for_status()
            data = response.json()
            
            content = data["choices"][0]["message"]["content"]
            
            # Extract JSON from response
            json_start = content.find("{")
            json_end = content.rfind("}")
            if json_start >= 0 and json_end > json_start:
                result = json.loads(content[json_start:json_end + 1])
                
                confidence = result.get("confidence", 0.5)
                if confidence > 0.7:
                    return IntentClassification(
                        intent=IntentCategory(result.get("intent", "unclear")),
                        confidence=confidence,
                        extracted_entities=result.get("extracted_entities", {}),
                        clarification_needed=result.get("clarification_needed", False),
                        clarification_prompt=result.get("clarification_prompt"),
                        suggested_flow=result.get("suggested_flow")
                    )
                    
        except Exception as e:
            print(f"LLM classification failed: {e}")
        
        # Fallback to pattern matching
        return self._pattern_classify(message)
    
    def _pattern_classify(self, message: str) -> IntentClassification:
        """Classify using regex patterns."""
        for intent, patterns in self.fallback_patterns.items():
            for pattern in patterns:
                if pattern.search(message):
                    return IntentClassification(
                        intent=intent,
                        confidence=0.75,
                        extracted_entities={"matched_pattern": pattern.pattern}
                    )
        
        # Check for small talk patterns
        if len(message.split()) < 5 and not any(c in message for c in ["/", "$", "@"]):
            return IntentClassification(
                intent=IntentCategory.SMALL_TALK,
                confidence=0.6
            )
        
        return IntentClassification(
            intent=IntentCategory.UNCLEAR,
            confidence=0.5,
            clarification_needed=True,
            clarification_prompt="I'm not sure what you'd like to do. You can upload receipts, check your HSA balance, or import multiple files. What would you like to try?"
        )
    
    def extract_directory_path(self, message: str) -> Optional[str]:
        """Extract directory path from message."""
        # Match common path patterns
        path_patterns = [
            r'(?:from|in|at)\s+([/~]?[\w\-/.]+)',
            r'([/~][\w\-/.]+)',
        ]
        for pattern in path_patterns:
            match = re.search(pattern, message)
            if match:
                return match.group(1)
        return None
