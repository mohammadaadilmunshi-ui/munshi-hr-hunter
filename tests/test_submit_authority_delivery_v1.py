"""Canonical-authority delivery to Apply, and the two anti-drift proofs.

Delivery reuses the already-authenticated Apply command channel; it is
best-effort because Apply can also pull the identical sealed envelope over the
Hunter bridge. These tests pin BOTH directions of that contract so neither side
can silently diverge, and so Apply can never pass a fixture that merely agrees
with its own invented secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app import application_submit_authorization_v1 as auth
from app import prepare_worker_adapters_v1c as v1c

TEST_SECRET = "test-only-submit-authority-secret-not-a-real-key-0123456789"


def _envelope(**overrides) -> dict:
    envelope = {
        "version": auth.AUTH_VERSION,
        "authorization_id": "submit-auth-0000000000000000000000000000000000000001",
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "plan_id": "application-plan-1",
        "session_id": "apply-session-1",
        "review_id": "review-v2-" + "b" * 32,
        "approval_id": "review-approval-1",
        "provider": "GREENHOUSE",
        "target_url": "https://boards.greenhouse.io/example/jobs/42",
        "checkpoint_id": "checkpoint-1",
        "generation": 1,
        "plan_digest": "a" * 64,
        "review_digest": "b" * 64,
        "approval_digest": "c" * 64,
        "prepared_package_digest": "d" * 64,
        "browser_form_digest": "e" * 64,
        "resume_sha256": "f" * 64,
        "cover_letter_sha256": None,
        "issued_at": "2026-09-14T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "synthetic": False,
        "submission_authority": True,
    }
    material = {k: v for k, v in envelope.items()}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    envelope["authority_digest"] = digest
    envelope["signature"] = (
        "sha256="
        + hmac.new(TEST_SECRET.encode(), digest.encode(), hashlib.sha256).hexdigest()
    )
    envelope.update(overrides)
    return envelope


def _ack(envelope: dict) -> dict:
    return {
        "accepted": True,
        "authorization_id": envelope["authorization_id"],
        "session_id": envelope["session_id"],
        "state": "RECEIVED",
    }


def test_delivery_is_default_off_and_reports_not_attempted(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(v1c.AUTHORITY_DELIVERY_ENV, raising=False)
    calls: list = []
    monkeypatch.setattr(v1c, "_json_request", lambda **kwargs: calls.append(kwargs))
    assert v1c.deliver_submit_authorization(_envelope()) == {
        "attempted": False,
        "delivered": False,
        "error": None,
    }
    assert calls == []


def test_delivery_posts_the_exact_envelope_to_the_bound_session(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://127.0.0.1:8505")
    monkeypatch.setenv(v1c.APPLY_COMMAND_SECRET_ENV, "test-command-secret")
    captured: dict = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return _ack(kwargs["body"])

    monkeypatch.setattr(v1c, "_json_request", fake_request)
    envelope = _envelope()
    result = v1c.deliver_submit_authorization(envelope)

    assert result["delivered"] is True
    assert captured["url"].endswith(
        "/v1/complete-loop/sessions/apply-session-1/submit-authority"
    )
    # The body is the 26-key envelope verbatim: Apply recomputes the digest and
    # refuses any re-serialisation, so re-shaping here would break the loop.
    assert captured["body"] == envelope
    assert captured["require_ats_runtime"] is False
    assert captured["headers"]["X-Munshi-Tenant-Id"] == "default"
    assert captured["headers"]["X-Munshi-User-Id"] == "local-owner"


def test_delivery_failure_is_reported_without_touching_the_approval(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed push must never be able to undo the customer's single approval."""
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://127.0.0.1:8505")
    monkeypatch.setenv(v1c.APPLY_COMMAND_SECRET_ENV, "test-command-secret")

    def boom(**kwargs):
        raise RuntimeError("Apply runtime transport failed.")

    monkeypatch.setattr(v1c, "_json_request", boom)
    result = v1c.deliver_submit_authorization(_envelope())
    assert result["attempted"] is True
    assert result["delivered"] is False
    assert "transport failed" in str(result["error"])


@pytest.mark.parametrize(
    "ack_mutation",
    [
        {"accepted": False},
        {"authorization_id": "submit-auth-forged"},
        {"session_id": "another-session"},
    ],
)
def test_uncorrelated_acknowledgement_is_rejected(
    monkeypatch: pytest.MonkeyPatch, ack_mutation: dict
) -> None:
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://127.0.0.1:8505")
    monkeypatch.setenv(v1c.APPLY_COMMAND_SECRET_ENV, "test-command-secret")
    envelope = _envelope()
    ack = {**_ack(envelope), **ack_mutation}
    monkeypatch.setattr(v1c, "_json_request", lambda **kwargs: ack)
    result = v1c.deliver_submit_authorization(envelope)
    assert result["delivered"] is False
    assert "not correlated" in str(result["error"])


def test_missing_session_binding_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    result = v1c.deliver_submit_authorization(_envelope(session_id="  "))
    assert result["attempted"] is False
    assert "session" in str(result["error"]).lower()


def test_explicit_private_apply_bridge_allows_internal_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_COMMAND_SECRET_ENV, "test-command-secret")
    monkeypatch.setenv(v1c.APPLY_INTERNAL_HTTP_ENABLED_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_INTERNAL_HTTP_BASE_URL_ENV, "http://apply:8000")
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://apply:8000")
    monkeypatch.setattr(v1c, "_json_request", lambda **kwargs: _ack(kwargs["body"]))
    assert v1c.deliver_submit_authorization(_envelope())["delivered"] is True

    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://not-apply:8000")
    result = v1c.deliver_submit_authorization(_envelope())
    assert result["delivered"] is False
    assert "allowlisted private Docker bridge" in str(result["error"])


def test_non_loopback_delivery_requires_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(v1c.AUTHORITY_DELIVERY_ENV, "true")
    monkeypatch.setenv(v1c.APPLY_COMMAND_SECRET_ENV, "test-command-secret")
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://apply.example.test")
    monkeypatch.setattr(v1c, "_json_request", lambda **kwargs: _ack(kwargs["body"]))
    result = v1c.deliver_submit_authorization(_envelope())
    assert result["attempted"] is True and result["delivered"] is False
    assert "HTTPS" in str(result["error"])

    # Loopback HTTP stays allowed for local verification only.
    monkeypatch.setenv(v1c.APPLY_BASE_URL_ENV, "http://localhost:8505")
    assert v1c.deliver_submit_authorization(_envelope())["delivered"] is True


def test_approval_and_submit_records_pre_browser_execution_mandate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sole customer click records a mandate; browser submit authority comes later."""
    from app import application_execution_mandate_v1 as mandate_v1
    from app import product_application_flow_v2 as flow

    review = {
        "preflight_package_id": "preflight-package-1",
        "review_digest": "b" * 64,
    }
    preflight = {
        "preflight_package_id": "preflight-package-1",
        "cover_letter_decision": "NOT_REQUIRED",
    }
    monkeypatch.setattr(
        flow,
        "flow_for_job",
        lambda job_id: {
            "state": "READY_FOR_APPROVAL",
            "package_review": dict(review),
            "artifacts": {"preflight": dict(preflight)},
        },
    )
    reservations = []
    monkeypatch.setattr(
        flow,
        "_reserve_native_route_for_approval",
        lambda **kwargs: (
            reservations.append(dict(kwargs)) or {"provider": "NATIVE", "replayed": False}
        ),
    )
    monkeypatch.setattr(
        mandate_v1,
        "approve_package",
        lambda **kwargs: {
            "mandate_id": "execution-mandate-1",
            "preflight_package_id": kwargs["preflight_package_id"],
            "review_digest": kwargs["expected_review_digest"],
        },
    )

    result = flow.approve_and_submit_for_job(42)
    assert result["status"] == "PREPARING_ATS"
    assert result["browser_execution_started"] is False
    assert result["employer_receipt_verified"] is False
    assert reservations[0]["job_id"] == 42
    assert reservations[0]["preflight_package_id"] == "preflight-package-1"
    assert result["customer_approval_complete"] is True
    assert result["additional_customer_approval_required"] is False
    assert result["submit_authorization"] is None
    assert result["submission_authority"] is False
    assert "authority_delivery" not in result
