from __future__ import annotations

import json

import pytest

from app import application_plan_v2


def test_provider_mailbox_policy_is_exact_normalized_and_mandatory(monkeypatch):
    monkeypatch.delenv(application_plan_v2.MAILBOX_POLICY_ENV, raising=False)
    policy = application_plan_v2._normalize_provider_policy(
        {
            "mailbox_verification": {
                "sender_domains": ["MAIL.EXAMPLE.COM.", "mail.example.com"],
                "link_hosts": ["https://Verify.Example.com/path"],
            }
        },
        provider="GREENHOUSE",
    )
    assert policy["mailbox_verification"] == {
        "sender_domains": ["mail.example.com"],
        "link_hosts": ["verify.example.com"],
        "mandatory": True,
    }


def test_provider_mailbox_policy_can_come_from_runtime_configuration(monkeypatch):
    monkeypatch.setenv(
        application_plan_v2.MAILBOX_POLICY_ENV,
        json.dumps(
            {
                "WORKDAY": {
                    "sender_domains": ["mail.example-workday.com"],
                    "link_hosts": ["wd5.myworkdayjobs.com"],
                }
            }
        ),
    )
    policy = application_plan_v2._normalize_provider_policy(None, provider="WORKDAY")
    assert policy["mailbox_verification"]["mandatory"] is True
    assert policy["mailbox_verification"]["sender_domains"] == [
        "mail.example-workday.com"
    ]


def test_provider_mailbox_policy_rejects_wildcards_and_missing_senders(monkeypatch):
    monkeypatch.delenv(application_plan_v2.MAILBOX_POLICY_ENV, raising=False)
    with pytest.raises(ValueError, match="invalid exact domain"):
        application_plan_v2._normalize_provider_policy(
            {
                "mailbox_verification": {
                    "sender_domains": ["*.example.com"],
                    "link_hosts": ["greenhouse.io"],
                }
            },
            provider="GREENHOUSE",
        )
    with pytest.raises(ValueError, match="sender_domains"):
        application_plan_v2._normalize_provider_policy(
            {"mailbox_verification": {"link_hosts": ["greenhouse.io"]}},
            provider="GREENHOUSE",
        )
