from __future__ import annotations

import base64

from app.ats_managed_identity import prepare_managed_account
from app.ats_verification_orchestrator import (
    begin_verification,
    mark_consumed,
    observe_message_metadata,
)


def _setup(monkeypatch):
    monkeypatch.setenv(
        "MUNSHI_VAULT_KEY",
        base64.urlsafe_b64encode(b"v" * 32).decode(),
    )
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_DOMAIN", "apply.munshi.systems")
    monkeypatch.setenv("MUNSHI_APPLICATION_MAIL_RELAY_ENABLED", "1")
    return prepare_managed_account(
        provider="workday",
        account_scope="example.myworkdayjobs.com/example",
        label="Example Workday",
    )


def test_expected_ordinary_verification_link_can_be_auto_followed(hunter_db, monkeypatch):
    account = _setup(monkeypatch)
    request = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
        application_key="application-1",
    )
    assert request["mail_relay_ready"] is True
    assert request["recipient_email"] == account["application_email"]

    observed = observe_message_metadata(
        request_id=request["request_id"],
        recipient_email=request["recipient_email"],
        sender_domain="myworkday.com",
        observed_link_host="https://wd5.myworkday.com",
    )
    assert observed["state"] == "LINK_READY"
    assert observed["auto_follow_allowed"] is True
    assert observed["security_checkpoint"] is False
    assert observed["verification_url_stored"] is False

    assert mark_consumed(request_id=request["request_id"])["state"] == "CONSUMED"


def test_security_code_is_never_treated_as_ordinary_email_verification(hunter_db, monkeypatch):
    account = _setup(monkeypatch)
    request = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
    )
    observed = observe_message_metadata(
        request_id=request["request_id"],
        recipient_email=request["recipient_email"],
        sender_domain="myworkday.com",
        observed_link_host="wd5.myworkday.com",
        contains_security_code=True,
    )
    assert observed["state"] == "SECURITY_CHECKPOINT"
    assert observed["auto_follow_allowed"] is False
    assert observed["security_checkpoint"] is True


def test_unexpected_sender_is_rejected_even_with_expected_link_host(hunter_db, monkeypatch):
    account = _setup(monkeypatch)
    request = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
    )
    observed = observe_message_metadata(
        request_id=request["request_id"],
        recipient_email=request["recipient_email"],
        sender_domain="phishing.example",
        observed_link_host="wd5.myworkday.com",
    )
    assert observed["state"] == "REJECTED"
    assert observed["reason_code"] == "UNEXPECTED_SENDER_DOMAIN"
    assert observed["auto_follow_allowed"] is False


def test_unexpected_link_host_is_rejected(hunter_db, monkeypatch):
    account = _setup(monkeypatch)
    request = begin_verification(
        account_id=account["account_id"],
        provider="workday",
        expected_link_hosts=["wd5.myworkday.com"],
        expected_sender_domains=["myworkday.com"],
    )
    observed = observe_message_metadata(
        request_id=request["request_id"],
        recipient_email=request["recipient_email"],
        sender_domain="myworkday.com",
        observed_link_host="evil.example",
    )
    assert observed["state"] == "REJECTED"
    assert observed["reason_code"] == "UNEXPECTED_VERIFICATION_HOST"
    assert observed["auto_follow_allowed"] is False
