"""Regression tests for chat session context fields used by router helpers."""

from datetime import datetime, timezone

from vivian_api.chat.session import SessionContext


def test_session_context_includes_recent_intent_fields():
    context = SessionContext()

    context.last_intent = "balance_query"
    now = datetime.now(timezone.utc)
    context.last_balance_query_time = now
    context.last_balance_query_result = {"balance": 12.34}
    context.last_charitable_query_time = now
    context.last_charitable_query_result = {"total_amount": 99.0}

    assert context.last_intent == "balance_query"
    assert context.last_balance_query_time == now
    assert context.last_balance_query_result == {"balance": 12.34}
    assert context.last_charitable_query_time == now
    assert context.last_charitable_query_result == {"total_amount": 99.0}
