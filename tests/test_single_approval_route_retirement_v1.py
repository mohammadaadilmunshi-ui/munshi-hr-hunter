"""The retired second-approval endpoint must stay retired and not wedge the loop."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault(
    "HUNTER_API_SECRET", "synthetic-test-secret-not-a-real-credential-20260914"
)

from fastapi.testclient import TestClient  # noqa: E402

from app import api  # noqa: E402

HEADERS = {"x-hunter-secret": os.environ["HUNTER_API_SECRET"]}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "idempotency_key": "some-other-key",
            "expected_review_digest": "b" * 64,
            "expected_prepared_package_digest": "d" * 64,
            "expected_browser_form_digest": "e" * 64,
        },
    ],
)
def test_separate_approval_endpoint_is_gone(payload: dict) -> None:
    """Approving without minting authority must be impossible by construction."""
    client = TestClient(api.app)
    response = client.post(
        "/api/application-reviews/review-v2-abc/approve",
        json=payload,
        headers=HEADERS,
    )
    assert response.status_code == 410
    detail = str(response.json().get("detail", ""))
    assert "approve-and-submit" in detail


def test_late_browser_bound_approve_and_submit_is_gone() -> None:
    client = TestClient(api.app)
    response = client.post(
        "/api/application-reviews/review-v2-abc/approve-and-submit",
        json={
            "session_id": "apply-session-1",
            "approval_idempotency_key": "retired-late-approval",
            "authorization_idempotency_key": "retired-late-approval",
            "expected_review_digest": "b" * 64,
            "expected_prepared_package_digest": "d" * 64,
            "expected_browser_form_digest": "e" * 64,
        },
        headers=HEADERS,
    )
    assert response.status_code == 410
    assert "pre-browser" in str(response.json().get("detail", "")).casefold()


def test_canonical_single_approval_route_is_prebrowser_job_package_route(
    monkeypatch,
) -> None:
    from app import product_application_flow_v2

    monkeypatch.setattr(
        product_application_flow_v2,
        "approve_and_submit_for_job",
        lambda job_id: {
            "mandate_id": f"execution-mandate-{job_id}",
            "status": "APPLYING_NOW",
            "customer_approval_complete": True,
            "additional_customer_approval_required": False,
            "submission_authority": False,
        },
    )
    client = TestClient(api.app)
    response = client.post(
        "/api/jobs/94/approve-and-submit",
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["mandate_id"] == "execution-mandate-94"
    assert response.json()["customer_approval_complete"] is True


def test_no_other_approval_route_is_registered() -> None:
    paths = {
        str(getattr(route, "path", ""))
        for route in api.app.routes
        if "approve" in str(getattr(route, "path", "")).casefold()
    }
    assert paths == {
        "/api/application-reviews/{review_id}/approve",
        "/api/application-reviews/{review_id}/approve-and-submit",
        "/api/jobs/{job_id}/approve-and-submit",
    }
