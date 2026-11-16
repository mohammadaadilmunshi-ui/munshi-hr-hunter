"""Regression coverage for P0 real-world browser incident, no external submits."""
from __future__ import annotations

import pytest

from app import application_provider_router_v1 as router
from app import application_view_model_v1 as view
from app import product_application_flow_v2 as flow
from app.sponsorship_guard_v1 import explicit_no_future_sponsorship

KRUSH = (
    "At this time, KRUSH Tech is unable to provide visa sponsorship for this role. "
    "Candidates must be authorized to work in the U.S. without the need for "
    "employer-sponsored visa support, now or in the future."
)


@pytest.mark.parametrize(("statement", "blocked"), [
    (KRUSH, True),
    ("Visa sponsorship is not available for this position.", True),
    ("We do not sponsor H-1B visas for this role.", True),
    ("We cannot offer sponsorship for this role.", True),
    ("We are able to provide visa sponsorship to employees.", False),
    ("Must be eligible to work in the United States.", False),
    ("Applicants do not require sponsorship.", False),
])
def test_sponsorship_text_requires_explicit_employer_ban(statement, blocked):
    assert bool(explicit_no_future_sponsorship({"description_raw": statement})) is blocked


def test_router_blocks_explicit_no_sponsorship_even_when_legacy_row_eligible():
    job = {
        "id": 8488, "title": "Recruiting Coordinator",
        "company_name": "KRUSH Tech", "status": "found",
        "primary_decision": "ELIGIBLE", "hard_rejection_reason": None,
        "already_applied": 0, "description_raw": KRUSH,
    }
    with pytest.raises(PermissionError, match="excludes visa sponsorship"):
        router._assert_eligible(object(), job)


@pytest.mark.parametrize(("claim_state", "expected_state", "expected_authority"), [
    ("UNKNOWN", "OUTCOME_UNKNOWN", "relay_outcome_unknown"),
    ("CLAIMED", "SUBMITTING", "relay_outcome_pending"),
])
def test_relay_claim_state_not_confused_with_active_submit(
    monkeypatch, claim_state, expected_state, expected_authority
):
    route = {
        "provider": "TSENTA", "state": "PREPARATION",
        "dispatch_claim_exists": True, "dispatch_claim_state": claim_state,
    }
    monkeypatch.setattr(flow, "_latest_prepare", lambda *_: None)
    monkeypatch.setattr(flow, "_latest_lifecycle", lambda *_: None)
    monkeypatch.setattr(flow, "_provider_route", lambda *_: route)
    monkeypatch.setattr(flow, "_execution_mandate", lambda *args, **kwargs: None)
    monkeypatch.setattr(flow, "_production_receipt", lambda *_: None)
    result = flow.flow_for_job(8518, summary=True)
    assert result["state"] == expected_state
    assert result["authority"] == expected_authority
    if claim_state == "UNKNOWN":
        assert "unknown" in result["display_status"].lower()
        assert result["cta_label"] == "Verify existing attempt"
        model = view.view_for_job(
            8518,
            seed={"id": 8518, "company_name": "OneTouch Direct", "title": "Recruiter"},
            flow_seed=result,
            heartbeat={"health": "ok"},
            visibility_seed={},
        )
        assert model.human_state == "Outcome unknown · do not resubmit"
        assert model.state_group == "needs_attention"
        assert model.submission_attempted
        assert model.allowed_recovery_actions == ("refresh_state", "clear_local_selection")
        assert "never resubmit" in model.next_action.lower()
    else:
        assert result["display_status"] == "Submission outcome pending"
