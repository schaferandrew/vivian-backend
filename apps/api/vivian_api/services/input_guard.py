"""Input preprocessing for LLM safety: PII redaction and prompt-injection filtering."""

from __future__ import annotations

import re
from dataclasses import dataclass


_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"(?im)^\s*(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)"),
    re.compile(r"(?im)^\s*(system\s+prompt|developer\s+message|hidden\s+instructions?)\b"),
    re.compile(r"(?im)\b(reveal|show|print|leak)\b.{0,40}\b(system\s+prompt|instructions?)\b"),
    re.compile(r"(?im)^\s*you\s+are\s+(chatgpt|an\s+llm|a\s+language\s+model)\b"),
    re.compile(r"(?im)<\|im_start\|>|<\|im_end\|>|```\s*(system|assistant|developer)"),
)

_PII_PATTERNS = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_CARD]"),
    (re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization)\s*[:=]\s*\S+"), "[REDACTED_SECRET]"),
)


@dataclass(slots=True)
class ProcessedInput:
    """Result of sanitizing user-controlled content for LLM calls."""

    text: str
    pii_redacted: bool
    prompt_injection_filtered: bool



def sanitize_text_for_llm(text: str) -> ProcessedInput:
    """Redact common PII/secrets and filter likely prompt-injection directives."""
    safe_text = text or ""
    pii_redacted = False
    prompt_injection_filtered = False

    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(safe_text):
            prompt_injection_filtered = True
            safe_text = pattern.sub("[FILTERED_PROMPT_INJECTION]", safe_text)

    for pattern, replacement in _PII_PATTERNS:
        updated = pattern.sub(replacement, safe_text)
        if updated != safe_text:
            pii_redacted = True
            safe_text = updated

    return ProcessedInput(
        text=safe_text,
        pii_redacted=pii_redacted,
        prompt_injection_filtered=prompt_injection_filtered,
    )
