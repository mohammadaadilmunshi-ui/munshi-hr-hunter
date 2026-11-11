"""One customer approval -> one exact production submit authorization.

The candidate's Approve & Submit action is the only human approval. Hashing,
freshness validation, approval persistence, and JIT authority issuance remain
internal security mechanics.
"""

from __future__ import annotations

from typing import Any

from app import application_review_v2
from app import application_submit_authorization_v1
from app.database import get_connection
from app.tenant_foundation import current_owner


def approve_and_authorize(
    *,
    review_id: str,
    session_id: str,
    approval_idempotency_key: str,
    authorization_idempotency_key: str,
    expected_review_digest: str,
    expected_prepared_package_digest: str,
    expected_browser_form_digest: str,
) -> dict[str, Any]:
    """Approve the displayed snapshot and mint its one-use JIT authority.

    A failure between the two durable operations is retryable with the same
    idempotency keys and never asks the candidate to approve a second time.
    """
    approval = application_review_v2.approve_review(
        review_id=review_id,
        idempotency_key=approval_idempotency_key,
        expected_review_digest=expected_review_digest,
        expected_prepared_package_digest=expected_prepared_package_digest,
        expected_browser_form_digest=expected_browser_form_digest,
    )
    review = application_review_v2.get_review(review_id)
    snapshot = dict(review["review"])
    bindings = dict(snapshot["bindings"])

    if (
        str(review["review_digest"]) != str(expected_review_digest)
        or str(review["prepared_package_digest"])
        != str(expected_prepared_package_digest)
        or str(review["browser_form_digest"]) != str(expected_browser_form_digest)
    ):
        raise ValueError("Displayed review bindings changed before submit authorization.")

    connection = get_connection()
    try:
        owner = current_owner(connection)
    finally:
        connection.close()

    authority = application_submit_authorization_v1.issue_authorization(
        {
            "idempotency_key": authorization_idempotency_key,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "application_id": str(review["application_id"]),
            "plan_id": str(review["plan_id"]),
            "session_id": session_id,
            "review_id": str(review["review_id"]),
            "approval_id": str(approval["approval_id"]),
            "provider": str(snapshot["provider"]),
            "target_url": str(snapshot["destination_url"]),
            "checkpoint_id": str(bindings["checkpoint_id"]),
            "plan_digest": str(review["plan_digest"]),
            "review_digest": str(review["review_digest"]),
            "approval_digest": str(approval["approval_digest"]),
            "prepared_package_digest": str(review["prepared_package_digest"]),
            "browser_form_digest": str(review["browser_form_digest"]),
            "resume_sha256": str(review["resume_artifact_sha256"]),
            "cover_letter_sha256": bindings.get(
                "cover_letter_artifact_sha256"
            ),
        }
    )
    return {
        "review_id": str(review_id),
        "approval_id": str(approval["approval_id"]),
        "authorization_id": str(authority["authorization_id"]),
        "status": "APPLYING_NOW",
        "customer_approval_complete": True,
        "additional_customer_approval_required": False,
        "submit_authorization": authority,
    }
