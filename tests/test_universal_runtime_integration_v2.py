from __future__ import annotations

import pytest

from app import application_plan_v2 as plans
from app import ats_verification_orchestrator as legacy_verification
from app import inbound_mail
from app import mailbox_verification_service as mailbox


def test_generic_provider_gets_mandatory_mailbox_policy_from_exact_target() -> None:
    policy = plans._normalize_provider_policy(
        None,
        provider="GENERIC",
        allowed_hosts=("jobs.example.com",),
    )
    assert policy["permitted"] is True
    assert policy["provider"] == "GENERIC"
    assert policy["provider_recipe"] is None
    assert policy["mailbox_verification"]["mandatory"] is True
    assert policy["allowed_hosts"] == ["jobs.example.com"]
    assert "jobs.example.com" in policy["mailbox_verification"]["link_hosts"]
    assert "example.com" in policy["mailbox_verification"]["link_hosts"]
    assert "example.com" in policy["mailbox_verification"]["sender_domains"]
    assert policy["account_identity"]["username_policy"] == "EMAIL"


def test_known_provider_policy_keeps_recipe_and_exact_destination() -> None:
    agile = plans._normalize_provider_policy(
        None,
        provider="AGILE_ATS",
        allowed_hosts=("neomax.jobs.agile-ats.com",),
    )
    assert agile["provider_recipe"] == "AGILE_ATS"
    assert agile["allowed_hosts"] == [
        "agile-ats.com",
        "neomax.jobs.agile-ats.com",
    ]
    assert "agile-ats.com" in agile["mailbox_verification"]["sender_domains"]


@pytest.mark.parametrize(
    "value",
    [
        {"mailbox_verification": {"mandatory": False}},
        {"account_identity": {"username_policy": "ARBITRARY_USERNAME"}},
        {"verification_sender_domains": ["127.0.0.1"]},
        {"verification_link_hosts": ["http://verify.example.com"]},
    ],
)
def test_mailbox_policy_rejects_unsafe_or_unsupported_configuration(value) -> None:
    with pytest.raises(ValueError):
        plans._normalize_provider_policy(
            value,
            provider="GENERIC",
            allowed_hosts=("jobs.example.com",),
        )


def test_sender_and_link_domain_matching_is_exact_or_subdomain_only() -> None:
    for matcher in (
        inbound_mail._domain_matches,
        mailbox._domain_matches,
        legacy_verification._domain_matches,
    ):
        assert matcher("mail.example.com", "example.com") is True
        assert matcher("example.com", "example.com") is True
        assert matcher("example.com.evil.test", "example.com") is False
        assert matcher("evil-example.com", "example.com") is False

    extracted = mailbox._extract_link(
        {
            "text_body": "Verify: https://verify.jobs.example.com/token/abc",
            "html_body": "",
        },
        {"example.com"},
    )
    assert extracted == "https://verify.jobs.example.com/token/abc"


def test_spoofed_known_provider_name_never_inherits_known_recipe() -> None:
    policy = plans._normalize_provider_policy(
        None,
        provider="GENERIC",
        allowed_hosts=("greenhouse.evil.test",),
    )
    assert policy["provider"] == "GENERIC"
    assert policy["provider_recipe"] is None
    assert "greenhouse.io" not in policy["allowed_hosts"]
