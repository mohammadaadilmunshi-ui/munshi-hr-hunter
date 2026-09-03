from __future__ import annotations

from app.gmail_integration import classify_message, upsert_message


def test_gmail_classification_is_conservative() -> None:
    assert classify_message("Interview invitation", "", "")[0] == "interview"
    assert classify_message("A note", "", "hello")[0] == "unclassified"


def test_gmail_message_deduplication(hunter_db) -> None:
    message = {"id": "gmail-message-1", "subject": "Application received", "sender": "jobs@example.test", "snippet": "Thank you for applying"}
    assert upsert_message(message) is True
    assert upsert_message(message) is False
