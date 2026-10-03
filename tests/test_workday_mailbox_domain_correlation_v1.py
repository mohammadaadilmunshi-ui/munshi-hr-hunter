from __future__ import annotations

from app import application_plan_v2 as plans
from app import ats_verification_orchestrator as orchestrator
from app import inbound_mail
from app import mailbox_verification_service as mailbox


def test_workday_policy_covers_real_workday_sender_parent() -> None:
    policy = plans._normalize_provider_policy(None, provider="WORKDAY")

    assert policy["mailbox_verification"]["sender_domains"] == [
        "myworkday.com",
        "workday.com",
    ]


def test_mail_domain_matching_accepts_true_subdomains_only() -> None:
    observed = "eu-west-1.bounce.otp.workday.com"

    for matcher in (
        inbound_mail._domain_matches,
        mailbox._domain_matches,
        orchestrator._domain_matches,
    ):
        assert matcher(observed, "workday.com") is True
        assert matcher("workday.com", "workday.com") is True
        assert matcher("workday.com.evil.test", "workday.com") is False
        assert matcher("evilworkday.com", "workday.com") is False
        assert matcher("workday.com.", "workday.com") is True
