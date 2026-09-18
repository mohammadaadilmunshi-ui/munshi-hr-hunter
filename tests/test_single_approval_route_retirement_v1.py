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


def test_canonical_single_approval_route_is_still_reachable() -> None:
    """The one customer boundary must remain routed (not accidentally removed)."""
    client = TestClient(api.app)
    response = client.post(
        "/api/application-reviews/review-v2-abc/approve-and-submit",
        json={
            "session_id": "apply-session-1",
            "approval_idempotency_key": "customer-approve-submit-1",
            "authorization_idempotency_key": "customer-approve-submit-1",
            "expected_review_digest": "b" * 64,
            "expected_prepared_package_digest": "d" * 64,
            "expected_browser_form_digest": "e" * 64,
        },
        headers=HEADERS,
    )
    # Unauthenticated against a real review fails closed (409), but is never 404/405/410.
    assert response.status_code not in {404, 405, 410}
    assert response.status_code in {200, 401, 409}


def test_no_other_approval_route_is_registered() -> None:
    paths = {
        str(getattr(route, "path", ""))
        for route in api.app.routes
        if "approve" in str(getattr(route, "path", "")).casefold()
    }
    assert paths == {
        "/api/application-reviews/{review_id}/approve",
        "/api/application-reviews/{review_id}/approve-and-submit",
    }
