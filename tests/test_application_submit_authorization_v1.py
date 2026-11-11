"""Canonical submit-authority contract: mint, verify, claim, replay, read.

The golden vector in this file is deliberately reproducible without importing Hunter.
Apply mirrors the same literal in
apps/native-host/tests/test_submit_authority_contract_vector.py so that a one-sided
change to the canonicalization or field set fails in exactly one repository.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import sqlite3
import time
from datetime import timedelta

import pytest

from app import application_artifact_transport_v2 as transport
from app import application_submit_authorization_v1 as auth
from app import database

TEST_SECRET = "test-only-submit-authority-secret-not-a-real-key-0123456789"

# ---------------------------------------------------------------------------
# Golden vector (shared verbatim with Apply)
# ---------------------------------------------------------------------------
MATERIAL = {
    "version": "munshi-submit-authorization-v1",
    "authorization_id": "submit-auth-0000000000000000000000000000000000000001",
    "tenant_id": "default",
    "user_id": "local-owner",
    "application_id": "application-1",
    "plan_id": "application-plan-1",
    "session_id": "apply-session-1",
    "review_id": "review-v2-" + "b" * 32,
    "approval_id": "review-approval-00000000-0000-4000-8000-000000000001",
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
    "expires_at": "2026-09-14T00:05:00+00:00",
    "synthetic": False,
    "submission_authority": True,
}
PINNED_CANONICAL_LENGTH = 1183
PINNED_AUTHORITY_DIGEST = (
    "02a7996421d82192c90d6820c0105a4af6b9bcf29fb995cdc6cf2833b553c75d"
)
PINNED_SIGNATURE = (
    "sha256=806edb27b5362d8fd5c5338d9dd87e39a161b0327c8d95ba8828e159ad22dfed"
)
PINNED_CLAIM_DIGEST = (
    "d9467b76684320a0f3431284dd2b04cce3ba2b1f12095b9b785c868c5b8a7f2c"
)
CLAIMANT = "apply-worker-1"


def _envelope() -> dict:
    envelope = dict(MATERIAL)
    envelope["authority_digest"] = PINNED_AUTHORITY_DIGEST
    envelope["signature"] = PINNED_SIGNATURE
    return envelope


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test-only key material; never sourced from a real environment file."""
    monkeypatch.setenv("MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET", TEST_SECRET)


@pytest.fixture
def authority_db(hunter_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(auth.application_review_v2.REVIEW_ENV, "true")
    monkeypatch.setenv("MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET", TEST_SECRET)

    def ensure(connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        connection = connection or database.get_connection()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_plans_v2 (
                       plan_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, application_id TEXT NOT NULL,
                       provider TEXT NOT NULL, status TEXT NOT NULL,
                       executable INTEGER NOT NULL, plan_digest TEXT NOT NULL,
                       snapshot_json TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_plan_revisions_v2 (
                       prior_plan_id TEXT NOT NULL, replacement_plan_id TEXT)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_reviews_v2 (
                       review_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, application_id TEXT NOT NULL,
                       plan_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                       review_version TEXT NOT NULL, plan_digest TEXT NOT NULL,
                       prepared_package_digest TEXT NOT NULL,
                       browser_form_digest TEXT NOT NULL,
                       resume_artifact_sha256 TEXT NOT NULL,
                       review_digest TEXT NOT NULL, review_json TEXT NOT NULL,
                       invalidated_at TEXT, invalidation_reason TEXT,
                       created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_review_approvals_v2 (
                       approval_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, review_id TEXT NOT NULL,
                       idempotency_key TEXT NOT NULL, approval_version TEXT NOT NULL,
                       review_digest TEXT NOT NULL, plan_digest TEXT NOT NULL,
                       prepared_package_digest TEXT NOT NULL,
                       browser_form_digest TEXT NOT NULL, approval_digest TEXT NOT NULL,
                       approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                       consumed_at TEXT, consumption_key TEXT)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS application_submit_authorizations_v1 (
                       authorization_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                       user_id TEXT NOT NULL, application_id TEXT NOT NULL,
                       plan_id TEXT NOT NULL, session_id TEXT NOT NULL,
                       review_id TEXT NOT NULL, approval_id TEXT NOT NULL,
                       provider TEXT NOT NULL, target_url TEXT NOT NULL,
                       checkpoint_id TEXT NOT NULL, generation INTEGER NOT NULL,
                       plan_digest TEXT NOT NULL, review_digest TEXT NOT NULL,
                       approval_digest TEXT NOT NULL,
                       prepared_package_digest TEXT NOT NULL,
                       browser_form_digest TEXT NOT NULL, resume_sha256 TEXT NOT NULL,
                       cover_letter_sha256 TEXT, authority_digest TEXT NOT NULL,
                       authority_json TEXT NOT NULL,
                       status TEXT NOT NULL CHECK(status IN
                         ('ISSUED','CLAIMED','CONSUMED','REVOKED','EXPIRED')),
                       issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                       claimed_at TEXT, claimed_by TEXT, claim_digest TEXT,
                       consumed_at TEXT, revoked_at TEXT, revocation_reason TEXT,
                       UNIQUE(tenant_id,user_id,application_id,session_id,generation),
                       UNIQUE(authority_digest))"""
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    ensure()
    monkeypatch.setattr(auth, "ensure_schema", ensure)

    def seed(*, invalidated: bool = False, superseded: bool = False) -> None:
        """Re-seed plan/review/approval state, deliberately PRESERVING minted authorities.

        Replay convergence must be proven against the authority that was already issued,
        so wiping that table here would silently defeat the point of those tests.
        """
        connection = database.get_connection()
        try:
            connection.execute("DELETE FROM application_review_approvals_v2")
            connection.execute("DELETE FROM application_reviews_v2")
            connection.execute("DELETE FROM application_plan_revisions_v2")
            connection.execute("DELETE FROM application_plans_v2")
            connection.execute(
                """INSERT INTO application_plans_v2
                       (plan_id,tenant_id,user_id,application_id,provider,status,
                        executable,plan_digest,snapshot_json)
                   VALUES (:plan_id,:tenant_id,:user_id,:application_id,:provider,
                           :status,:executable,:plan_digest,:snapshot_json)""",
                {
                    "plan_id": "application-plan-1",
                    "tenant_id": "default",
                    "user_id": "local-owner",
                    "application_id": "application-1",
                    "provider": "GREENHOUSE",
                    "status": "READY_TO_APPLY",
                    "executable": 1,
                    "plan_digest": "a" * 64,
                    "snapshot_json": "{}",
                },
            )
            connection.execute(
                """INSERT INTO application_reviews_v2
                       (review_id,tenant_id,user_id,application_id,plan_id,idempotency_key,
                        review_version,plan_digest,prepared_package_digest,
                        browser_form_digest,resume_artifact_sha256,review_digest,
                        review_json,invalidated_at,invalidation_reason)
                   VALUES (:review_id,:tenant_id,:user_id,:application_id,:plan_id,
                           :idempotency_key,:review_version,:plan_digest,
                           :prepared_package_digest,:browser_form_digest,
                           :resume_artifact_sha256,:review_digest,:review_json,
                           :invalidated_at,:invalidation_reason)""",
                {
                    "review_id": "review-v2-" + "b" * 32,
                    "tenant_id": "default",
                    "user_id": "local-owner",
                    "application_id": "application-1",
                    "plan_id": "application-plan-1",
                    "idempotency_key": "review-key",
                    "review_version": "munshi-application-review-v2",
                    "plan_digest": "a" * 64,
                    "prepared_package_digest": "d" * 64,
                    "browser_form_digest": "e" * 64,
                    "resume_artifact_sha256": "f" * 64,
                    "review_digest": "b" * 64,
                    "review_json": json.dumps(
                        {
                            "destination_url": MATERIAL["target_url"],
                            "bindings": {"checkpoint_id": MATERIAL["checkpoint_id"]},
                        }
                    ),
                    "invalidated_at": "2026-09-14 00:00:00" if invalidated else None,
                    "invalidation_reason": None,
                },
            )
            connection.execute(
                """INSERT INTO application_review_approvals_v2
                       (approval_id,tenant_id,user_id,review_id,idempotency_key,
                        approval_version,review_digest,plan_digest,
                        prepared_package_digest,browser_form_digest,approval_digest,
                        approved_at,consumed_at,consumption_key)
                   VALUES (:approval_id,:tenant_id,:user_id,:review_id,:idempotency_key,
                           :approval_version,:review_digest,:plan_digest,
                           :prepared_package_digest,:browser_form_digest,
                           :approval_digest,:approved_at,NULL,NULL)""",
                {
                    "approval_id": MATERIAL["approval_id"],
                    "tenant_id": "default",
                    "user_id": "local-owner",
                    "review_id": "review-v2-" + "b" * 32,
                    "idempotency_key": "approval-key",
                    "approval_version": "munshi-application-review-approval-v2",
                    "review_digest": "b" * 64,
                    "plan_digest": "a" * 64,
                    "prepared_package_digest": "d" * 64,
                    "browser_form_digest": "e" * 64,
                    "approval_digest": "c" * 64,
                    "approved_at": "2026-09-14 00:00:00",
                },
            )
            if superseded:
                connection.execute(
                    "INSERT INTO application_plan_revisions_v2 VALUES (?,?)",
                    ("application-plan-1", "application-plan-2"),
                )
            connection.commit()
        finally:
            connection.close()

    seed()
    return seed


def _payload(**overrides) -> dict:
    base = {
        "idempotency_key": "customer-approve-submit-1",
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "plan_id": "application-plan-1",
        "session_id": "apply-session-1",
        "review_id": "review-v2-" + "b" * 32,
        "approval_id": "review-approval-00000000-0000-4000-8000-000000000001",
        "provider": "GREENHOUSE",
        "target_url": MATERIAL["target_url"],
        "checkpoint_id": MATERIAL["checkpoint_id"],
        "plan_digest": "a" * 64,
        "review_digest": "b" * 64,
        "approval_digest": "c" * 64,
        "prepared_package_digest": "d" * 64,
        "browser_form_digest": "e" * 64,
        "resume_sha256": "f" * 64,
        "cover_letter_sha256": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# D1. Golden vector + cryptographic integrity
# ---------------------------------------------------------------------------
def test_authority_golden_vector_is_byte_reproducible() -> None:
    """Pins the exact canonicalization Apply must be able to reproduce independently."""
    canonical = json.dumps(
        dict(MATERIAL), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert len(canonical) == PINNED_CANONICAL_LENGTH
    assert sorted(MATERIAL) == sorted(
        [
            "application_id", "approval_digest", "approval_id", "authorization_id",
            "browser_form_digest", "checkpoint_id", "cover_letter_sha256",
            "expires_at", "generation", "issued_at", "plan_digest", "plan_id",
            "prepared_package_digest", "provider", "resume_sha256", "review_digest",
            "review_id", "session_id", "submission_authority", "synthetic",
            "target_url", "tenant_id", "user_id", "version",
        ]
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == PINNED_AUTHORITY_DIGEST
    expected = hmac.new(
        TEST_SECRET.encode(), PINNED_AUTHORITY_DIGEST.encode(), hashlib.sha256
    ).hexdigest()
    assert PINNED_SIGNATURE == f"sha256={expected}"


def test_material_builder_reproduces_the_pinned_envelope() -> None:
    """The pinned vector must be what the real builder emits, not a hand-tuned literal."""
    built = auth._material(
        _payload(),
        authorization_id=MATERIAL["authorization_id"],
        generation=1,
        issued_at=MATERIAL["issued_at"],
        expires_at=MATERIAL["expires_at"],
    )
    assert built == MATERIAL


def test_pinned_authority_verifies_and_produces_pinned_claim_digest() -> None:
    verified = auth.verify_envelope(_envelope(), require_fresh=False)
    assert verified["authorization_id"] == MATERIAL["authorization_id"]
    assert auth._claim_receipt(verified, CLAIMANT) == PINNED_CLAIM_DIGEST


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("target_url", "https://boards.greenhouse.io/other-employer/jobs/7"),
        ("checkpoint_id", "another-checkpoint"),
        ("session_id", "another-session"),
        ("application_id", "another-application"),
        ("tenant_id", "another-tenant"),
        ("user_id", "another-user"),
        ("plan_id", "another-plan"),
        ("review_id", "review-v2-" + "1" * 32),
        ("approval_id", "review-approval-" + "2" * 36),
        ("plan_digest", "1" * 64),
        ("review_digest", "2" * 64),
        ("approval_digest", "3" * 64),
        ("prepared_package_digest", "4" * 64),
        ("browser_form_digest", "5" * 64),
        ("resume_sha256", "6" * 64),
        ("generation", 99),
    ],
)
def test_any_bound_field_mutation_invalidates_authority(field: str, replacement) -> None:
    envelope = _envelope()
    assert envelope[field] != replacement, "test vector must actually differ"
    envelope[field] = replacement
    with pytest.raises(PermissionError):
        auth.verify_envelope(envelope, require_fresh=False)


def test_cover_letter_injection_invalidates_authority() -> None:
    envelope = _envelope()
    envelope["cover_letter_sha256"] = "9" * 64
    with pytest.raises(PermissionError):
        auth.verify_envelope(envelope, require_fresh=False)


def test_authority_flag_and_synthetic_flip_are_rejected() -> None:
    for field, value in (
        ("submission_authority", False),
        ("synthetic", True),
        ("version", "munshi-submit-authorization-v2"),
    ):
        envelope = _envelope()
        envelope[field] = value
        with pytest.raises(PermissionError):
            auth.verify_envelope(envelope, require_fresh=False)


def test_forged_signature_is_rejected() -> None:
    envelope = _envelope()
    envelope["signature"] = "sha256=" + "0" * 64
    with pytest.raises(PermissionError):
        auth.verify_envelope(envelope, require_fresh=False)


def test_expired_authority_is_rejected_but_still_verifiable_for_claim_replay() -> None:
    envelope = _envelope()
    with pytest.raises(PermissionError, match="expired"):
        auth.verify_envelope(envelope)
    # A claimant re-proving an already-won claim must survive envelope staleness;
    # the durable claim row, not the TTL, is what proves ownership.
    assert (
        auth.verify_envelope(envelope, require_fresh=False)["authorization_id"]
        == MATERIAL["authorization_id"]
    )


def test_unknown_extra_key_is_rejected_by_digest() -> None:
    envelope = _envelope()
    envelope["extra_permission"] = True
    with pytest.raises(PermissionError):
        auth.verify_envelope(envelope, require_fresh=False)


# ---------------------------------------------------------------------------
# D2. Issue / replay convergence
# ---------------------------------------------------------------------------
def test_issue_mints_exact_sealed_envelope(authority_db) -> None:
    envelope = auth.issue_authorization(_payload())
    assert set(envelope) == set(MATERIAL) | {"authority_digest", "signature"}
    assert envelope["synthetic"] is False and envelope["submission_authority"] is True
    assert envelope["authorization_id"].startswith("submit-auth-")
    verified = auth.verify_envelope(envelope)
    assert verified["authority_digest"] == envelope["authority_digest"]


def test_double_issue_converges_on_identical_envelope(authority_db) -> None:
    first = auth.issue_authorization(_payload())
    second = auth.issue_authorization(_payload())
    assert first == second


def test_issue_replay_conflict_when_cover_letter_changes(authority_db) -> None:
    auth.issue_authorization(_payload())
    with pytest.raises(ValueError, match="different bindings"):
        auth.issue_authorization(_payload(cover_letter_sha256="9" * 64))


def test_issue_replay_converges_after_review_invalidation(authority_db) -> None:
    """A lost response must converge even if the review/plan moved on meanwhile."""
    first = auth.issue_authorization(_payload())
    authority_db(invalidated=True)
    assert auth.issue_authorization(_payload()) == first
    # ... but a genuinely NEW authority cannot be minted against an invalidated review.
    with pytest.raises(PermissionError):
        auth.issue_authorization(_payload(idempotency_key="a-different-action"))


def test_issue_replay_converges_after_plan_supersession(authority_db) -> None:
    first = auth.issue_authorization(_payload())
    authority_db(superseded=True)
    assert auth.issue_authorization(_payload()) == first
    with pytest.raises(PermissionError):
        auth.issue_authorization(_payload(idempotency_key="another-action"))


def test_consumed_approval_cannot_mint_a_second_authority(authority_db) -> None:
    auth.issue_authorization(_payload())
    with pytest.raises(PermissionError, match="already been consumed"):
        auth.issue_authorization(_payload(idempotency_key="second-click-different-key"))


def test_new_generation_revokes_prior_unused_authority(authority_db) -> None:
    first = auth.issue_authorization(_payload())
    # Same approval cannot re-issue; simulate the legitimate rollover path by
    # releasing the approval consumption so a new generation can exist.
    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE application_review_approvals_v2 SET consumed_at=NULL,consumption_key=NULL"
        )
        connection.commit()
    finally:
        connection.close()
    second = auth.issue_authorization(_payload(idempotency_key="rolled-over-action"))
    assert second["generation"] == first["generation"] + 1
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT status,revocation_reason FROM application_submit_authorizations_v1 WHERE authorization_id=?",
            (first["authorization_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row["status"] == "REVOKED"
    assert row["revocation_reason"] == "superseded_by_new_authorization_generation"


# ---------------------------------------------------------------------------
# D3. Destination binding (the customer approved exactly one target)
# ---------------------------------------------------------------------------
def test_target_url_swap_is_rejected(authority_db) -> None:
    with pytest.raises(PermissionError, match="immutable review bindings"):
        auth.issue_authorization(
            _payload(target_url="https://boards.greenhouse.io/other-employer/jobs/7")
        )


def test_checkpoint_swap_is_rejected(authority_db) -> None:
    with pytest.raises(PermissionError, match="immutable review bindings"):
        auth.issue_authorization(_payload(checkpoint_id="another-checkpoint"))


def test_wrong_application_is_rejected(authority_db) -> None:
    with pytest.raises(PermissionError, match="binding is invalid"):
        auth.issue_authorization(_payload(application_id="application-OTHER"))


# ---------------------------------------------------------------------------
# D4. Claim: one winner, convergent for the winner
# ---------------------------------------------------------------------------
def test_claim_is_atomic_and_single_use(authority_db) -> None:
    envelope = auth.issue_authorization(_payload())
    won = auth.claim_authorization(envelope, claimant_id="winner")
    assert won["status"] == "CLAIMED" and won["submission_authority"] is True
    assert won["replayed"] is False
    with pytest.raises(PermissionError, match="atomic race|already been claimed"):
        auth.claim_authorization(envelope, claimant_id="loser")


def test_claim_by_same_claimant_converges_after_lost_response(authority_db) -> None:
    envelope = auth.issue_authorization(_payload())
    first = auth.claim_authorization(envelope, claimant_id="apply-worker")
    replay = auth.claim_authorization(envelope, claimant_id="apply-worker")
    assert replay["replayed"] is True
    assert replay["claim_digest"] == first["claim_digest"]
    assert replay["status"] in ("CLAIMED", "CONSUMED")


def test_claim_rejects_tampered_envelope(authority_db) -> None:
    envelope = auth.issue_authorization(_payload())
    tampered = copy.deepcopy(envelope)
    tampered["target_url"] = "https://boards.greenhouse.io/other-employer/jobs/7"
    with pytest.raises(PermissionError):
        auth.claim_authorization(tampered, claimant_id="apply-worker")


def test_claim_expires_stale_issued_authority(authority_db, monkeypatch) -> None:
    envelope = auth.issue_authorization(_payload(), ttl_seconds=30)
    monkeypatch.setattr(
        auth,
        "_now",
        lambda: auth._parse_time(envelope["expires_at"]) + timedelta(seconds=1),
    )
    with pytest.raises(PermissionError, match="expired"):
        auth.claim_authorization(envelope, claimant_id="apply-worker")
    connection = database.get_connection()
    try:
        status = connection.execute(
            "SELECT status FROM application_submit_authorizations_v1 WHERE authorization_id=?",
            (envelope["authorization_id"],),
        ).fetchone()["status"]
    finally:
        connection.close()
    assert status == "EXPIRED"


# ---------------------------------------------------------------------------
# D5. Read path (closes the delivery gap for Apply)
# ---------------------------------------------------------------------------
def test_current_authorization_returns_the_sealed_envelope(authority_db) -> None:
    issued = auth.issue_authorization(_payload())
    assert auth.current_authorization(_read_payload()) == issued


def test_current_authorization_never_mints_or_extends(authority_db) -> None:
    issued = auth.issue_authorization(_payload())
    again = auth.current_authorization(_read_payload())
    assert again["expires_at"] == issued["expires_at"]
    assert again["authorization_id"] == issued["authorization_id"]


def test_current_authorization_requires_issued_status(authority_db) -> None:
    issued = auth.issue_authorization(_payload())
    auth.claim_authorization(issued, claimant_id="apply-worker")
    with pytest.raises(LookupError):
        auth.current_authorization(_read_payload())


def test_current_authorization_rejects_plan_digest_mismatch(authority_db) -> None:
    auth.issue_authorization(_payload())
    # A well-formed but wrong digest is a binding violation, not a format error.
    with pytest.raises(PermissionError, match="plan_digest"):
        auth.current_authorization(_read_payload(plan_digest="9" * 64))


def test_current_authorization_rejects_other_application(authority_db) -> None:
    auth.issue_authorization(_payload())
    with pytest.raises(LookupError):
        auth.current_authorization(_read_payload(application_id="application-OTHER"))


def _read_payload(**overrides) -> dict:
    base = {
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "plan_id": "application-plan-1",
        "plan_digest": "a" * 64,
        "session_id": "apply-session-1",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# D6. Default-off posture
# ---------------------------------------------------------------------------
def test_authority_operations_are_default_off(monkeypatch) -> None:
    monkeypatch.delenv(auth.application_review_v2.REVIEW_ENV, raising=False)
    for call in (
        lambda: auth.issue_authorization(_payload()),
        lambda: auth.claim_authorization(_envelope(), claimant_id="x"),
        lambda: auth.current_authorization(_read_payload()),
    ):
        with pytest.raises(RuntimeError, match="disabled"):
            call()


def test_missing_signing_secret_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_PRODUCTION_SUBMIT_AUTH_HMAC_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        auth.verify_envelope(_envelope(), require_fresh=False)


# ---------------------------------------------------------------------------
# D7. Delivery arrow: the sealed envelope is retrievable over the authenticated
# bridge. This is the seam that previously did not exist at all.
# ---------------------------------------------------------------------------
HANDOFF_SECRET = "test-only-apply-handoff-secret-not-a-real-key-0123456789"


def _bridge_headers(body: bytes, *, event_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    content_sha = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        HANDOFF_SECRET.encode(),
        f"{event_id}.{timestamp}.{content_sha}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Munshi-Event-Id": event_id,
        "X-Munshi-Timestamp": timestamp,
        "X-Munshi-Content-SHA256": content_sha,
        "X-Munshi-Signature": f"sha256={signature}",
    }


def _bridge_body(*, outer: dict | None = None, **payload_overrides) -> bytes:
    request = {
        "version": "munshi-application-execution-request-v1",
        "request_id": "bridge-read-1",
        "purpose": "SUBMIT_AUTHORIZATION_READ",
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "plan_id": "application-plan-1",
        "plan_digest": "a" * 64,
        "payload": _read_payload(**payload_overrides),
    }
    request.update(outer or {})
    return json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def test_authority_is_retrievable_over_the_authenticated_bridge(
    authority_db, monkeypatch
) -> None:
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", HANDOFF_SECRET)
    issued = auth.issue_authorization(_payload())

    body = _bridge_body()
    response_body, response_headers = transport.current_plan_response(
        body, _bridge_headers(body, event_id="bridge-read-1")
    )
    payload = json.loads(response_body)
    assert payload["purpose"] == "SUBMIT_AUTHORIZATION_READ"
    assert payload["result"] == issued

    # The response signature must bind to the exact body, purpose and plan digest.
    expected = hmac.new(
        HANDOFF_SECRET.encode(),
        "bridge-read-1.SUBMIT_AUTHORIZATION_READ.{}.{}".format(
            hashlib.sha256(response_body).hexdigest(), "a" * 64
        ).encode(),
        hashlib.sha256,
    ).hexdigest()
    assert response_headers["X-Munshi-Response-Signature"] == f"sha256={expected}"


def test_bridge_read_rejects_unsigned_and_non_canonical_requests(
    authority_db, monkeypatch
) -> None:
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", HANDOFF_SECRET)
    auth.issue_authorization(_payload())

    body = _bridge_body()
    headers = _bridge_headers(body, event_id="bridge-read-1")
    headers["X-Munshi-Signature"] = "sha256=" + "0" * 64
    with pytest.raises(PermissionError):
        transport.current_plan_response(body, headers)

    # Same request, non-canonical serialization must be refused outright.
    non_canonical = json.dumps(
        json.loads(body), sort_keys=True, indent=2
    ).encode()
    with pytest.raises(ValueError, match="canonical"):
        transport.current_plan_response(
            non_canonical, _bridge_headers(non_canonical, event_id="bridge-read-1")
        )


def test_bridge_read_cannot_cross_application_binding(authority_db, monkeypatch) -> None:
    """Two independent layers stop an authenticated caller reading a foreign authority."""
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", HANDOFF_SECRET)
    auth.issue_authorization(_payload())

    # Layer 1: the bridge refuses a request whose outer identity and payload identity
    # disagree, so the envelope cannot be smuggled against another application.
    smuggled = _bridge_body(application_id="application-OTHER")
    with pytest.raises(PermissionError, match="application_id"):
        transport.current_plan_response(
            smuggled, _bridge_headers(smuggled, event_id="bridge-read-1")
        )

    # Layer 2: even a perfectly self-consistent request for a different application
    # finds nothing, because the read is scoped to the exact stored bindings.
    foreign = _bridge_body(
        outer={"application_id": "application-OTHER"},
        application_id="application-OTHER",
    )
    with pytest.raises(LookupError):
        transport.current_plan_response(foreign, _bridge_headers(foreign, event_id="bridge-read-1"))
