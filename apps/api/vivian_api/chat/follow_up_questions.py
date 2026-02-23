"""Structured follow-up question helpers for missing tool input fields."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any


FIELD_SPECS: dict[str, dict[str, str]] = {
    "organization_name": {
        "label": "Organization name",
        "type": "text",
        "placeholder": "<Organization name>",
    },
    "donation_date": {
        "label": "Donation date",
        "type": "date",
        "placeholder": "<YYYY-MM-DD>",
    },
    "amount": {
        "label": "Donation amount",
        "type": "number",
        "placeholder": "<Amount>",
    },
    "cash_confirmation": {
        "label": "Cash donation?",
        "type": "text",
        "placeholder": "<yes/no>",
    },
}


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_non_empty(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _sanitize_candidate(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    if not normalized:
        return None
    if re.fullmatch(r"<[^>]+>", normalized):
        return None
    if normalized.lower() in {"organization name", "yyyy-mm-dd", "amount", "yes/no"}:
        return None
    return normalized


def _coerce_boolish(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if not isinstance(value, str):
        return None

    text = value.strip().lower()
    if not text:
        return None
    if text in {"true", "1", "yes", "y", "cash", "confirmed", "confirm"}:
        return True
    if text in {"false", "0", "no", "n", "not cash"}:
        return False
    if text in {"card", "credit", "credit card", "debit", "debit card", "check", "ach", "wire"}:
        return False
    return None


def build_missing_fields_follow_up_question(
    *,
    tool_name: str,
    server_id: str,
    arguments: dict[str, Any],
    raw_tool_output: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Build question + pending state from a tool output payload."""
    payload = _parse_json_object(raw_tool_output)
    if not payload:
        return None

    raw_missing = payload.get("missing_fields")
    if not isinstance(raw_missing, list):
        return None

    missing_fields = [str(item).strip() for item in raw_missing if str(item).strip()]
    if not missing_fields:
        return None

    fields: list[dict[str, Any]] = []
    for key in missing_fields:
        spec = FIELD_SPECS.get(key)
        if spec:
            fields.append(
                {
                    "key": key,
                    "label": spec["label"],
                    "type": spec["type"],
                    "required": True,
                    "placeholder": spec["placeholder"],
                }
            )
        else:
            fields.append(
                {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "type": "text",
                    "required": True,
                    "placeholder": "",
                }
            )

    suggested_values = payload.get("normalized_donation_json")
    if not isinstance(suggested_values, dict):
        suggested_values = {}

    pending_arguments = dict(arguments or {})
    if isinstance(pending_arguments.get("donation_json"), dict) and suggested_values:
        merged = dict(pending_arguments["donation_json"])
        for key, value in suggested_values.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged[key] = value
        pending_arguments["donation_json"] = merged

    prompt_base = str(payload.get("error") or "I need a few details before I can continue.").strip()
    if not prompt_base:
        prompt_base = "I need a few details before I can continue."

    template_lines = []
    for field in fields:
        key = field["key"]
        suggested = suggested_values.get(key)
        placeholder = field.get("placeholder") or "<value>"
        if isinstance(suggested, str) and suggested.strip():
            value = suggested.strip()
        elif suggested is not None and not isinstance(suggested, str):
            value = str(suggested)
        else:
            value = placeholder
        template_lines.append(f"{key}: {value}")
    template_text = "\n".join(template_lines)

    question_id = f"q_{uuid.uuid4().hex[:8]}"
    prompt = f"{prompt_base}\n\nPlease reply with:\n{template_text}"
    question = {
        "id": question_id,
        "kind": "missing_tool_fields",
        "server_id": server_id,
        "tool_name": tool_name,
        "prompt": prompt,
        "missing_fields": missing_fields,
        "fields": fields,
        "suggested_values": suggested_values,
    }
    pending = {
        **question,
        "arguments": pending_arguments,
    }
    return question, pending


def extract_donation_updates_from_message(
    message: str,
    missing_fields: list[str],
) -> dict[str, Any]:
    """Best-effort extraction of donation field updates from user free text."""
    text = (message or "").strip()
    if not text:
        return {}

    payload = _parse_json_object(text)
    if payload and isinstance(payload.get("donation_json"), dict):
        payload = payload["donation_json"]

    updates: dict[str, Any] = {}
    if isinstance(payload, dict):
        mapped_org = _sanitize_candidate(
            _first_non_empty(
                payload,
                ("organization_name", "organization", "org_name", "charity_name", "charity", "recipient"),
            )
        )
        if mapped_org is not None:
            updates["organization_name"] = mapped_org
        mapped_date = _sanitize_candidate(
            _first_non_empty(
                payload,
                ("donation_date", "date", "donationDate", "service_date", "paid_date"),
            )
        )
        if mapped_date is not None:
            updates["donation_date"] = mapped_date
        mapped_amount = _sanitize_candidate(_first_non_empty(payload, ("amount", "donation_amount", "total")))
        if mapped_amount is not None:
            updates["amount"] = mapped_amount

        cash_candidate = _sanitize_candidate(
            _first_non_empty(
                payload,
                (
                    "cash_confirmation",
                    "is_cash_donation",
                    "cash_donation",
                    "cash",
                    "payment_method",
                    "payment_type",
                ),
            )
        )
        if cash_candidate is not None:
            cash_confirmation = _coerce_boolish(cash_candidate)
            if cash_confirmation is not None:
                updates["cash_confirmation"] = cash_confirmation
        if "cash_confirmation" not in updates:
            has_receipt = _sanitize_candidate(
                _first_non_empty(payload, ("has_receipt", "receipt_available", "receipt_provided"))
            )
            receipt_bool = _coerce_boolish(has_receipt)
            if receipt_bool is not None:
                updates["cash_confirmation"] = not receipt_bool

    if "organization_name" not in updates:
        match = re.search(
            r"(?:organization(?:\s+name)?|org(?:\s+name)?|charity|recipient)\s*[:=]\s*([^\n,;]+)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            org_candidate = _sanitize_candidate(match.group(1).strip())
            if org_candidate is not None:
                updates["organization_name"] = org_candidate

    if "donation_date" not in updates:
        match = re.search(
            (
                r"(?:donation\s+date|date)\s*[:=]\s*("
                r"20\d{2}-\d{2}-\d{2}"
                r"|\d{1,2}/\d{1,2}/20\d{2}"
                r"|[A-Za-z]{3,9}\s+\d{1,2},\s*20\d{2}"
                r")"
            ),
            text,
            flags=re.IGNORECASE,
        )
        if match:
            updates["donation_date"] = match.group(1).strip()
        else:
            iso_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
            slash_date = re.search(r"\b(\d{1,2}/\d{1,2}/20\d{2})\b", text)
            if iso_date:
                updates["donation_date"] = iso_date.group(1)
            elif slash_date:
                updates["donation_date"] = slash_date.group(1)

    if "amount" not in updates:
        match = re.search(
            r"(?:amount|donation\s+amount|total)\s*[:=]\s*\$?\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            updates["amount"] = match.group(1)
        else:
            standalone = re.search(r"\$\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)", text)
            if standalone:
                updates["amount"] = standalone.group(1)

    if "cash_confirmation" not in updates:
        cash_labeled = re.search(
            r"(?:cash(?:\s+donation)?|cash_confirmation|is_cash_donation)\s*[:=]\s*([^\n,;]+)",
            text,
            flags=re.IGNORECASE,
        )
        if cash_labeled:
            cash_value = _coerce_boolish(cash_labeled.group(1))
            if cash_value is not None:
                updates["cash_confirmation"] = cash_value
        else:
            receipt_labeled = re.search(
                r"(?:has\s+receipt|receipt(?:\s+available|\s+provided)?)\s*[:=]\s*([^\n,;]+)",
                text,
                flags=re.IGNORECASE,
            )
            if receipt_labeled:
                receipt_value = _coerce_boolish(receipt_labeled.group(1))
                if receipt_value is not None:
                    updates["cash_confirmation"] = not receipt_value

    if (
        "organization_name" not in updates
        and len(missing_fields) == 1
        and missing_fields[0] == "organization_name"
    ):
        looks_like_scalar = bool(
            re.fullmatch(r"\$?\s*[0-9]+(?:\.[0-9]+)?", text)
            or re.fullmatch(r"20\d{2}-\d{2}-\d{2}", text)
            or re.fullmatch(r"\d{1,2}/\d{1,2}/20\d{2}", text)
        )
        if not looks_like_scalar and not text.startswith("{"):
            updates["organization_name"] = text

    if (
        "cash_confirmation" not in updates
        and len(missing_fields) == 1
        and missing_fields[0] == "cash_confirmation"
    ):
        scalar_cash = _coerce_boolish(text)
        if scalar_cash is not None:
            updates["cash_confirmation"] = scalar_cash

    return updates
