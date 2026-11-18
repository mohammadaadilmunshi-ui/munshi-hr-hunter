"""Safe, cross-platform application failure projection; never mutates tasks.

Employer and browser diagnostics may include names, credentials, form values or
provider URLs. Only known machine codes are projected to the customer.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_MESSAGE_BY_CODE = {
    "APPLICATION_PREFLIGHT_PREPARE_FAILED": (
        "Application package checks could not finish. The resume may be ready, "
        "but the package was not verified. No submission is confirmed."
    ),
    "APPLICATION_PREFLIGHT_VALIDATION_FAILED": (
        "The resume or saved application answers failed a package validation check. "
        "Review the prepared information before any safe retry."
    ),
    "APPLICATION_PREFLIGHT_REUSE_CHECK_FAILED": (
        "MUNSHI could not safely reuse an existing application package. "
        "No submission success was assumed."
    ),
    "APPLICATION_PREFLIGHT_OPPORTUNITY_FAILED": (
        "The job eligibility and opportunity checks could not be completed."
    ),
    "APPLICATION_PREFLIGHT_PACKAGE_FAILED": (
        "The application package could not be assembled or verified for this employer."
    ),
    "APPLICATION_PREFLIGHT_COVER_LETTER_FAILED": (
        "Required cover-letter generation or its evidence checks failed. "
        "The application was not confirmed as submitted."
    ),
    "APPLICATION_PREFLIGHT_FINALIZE_FAILED": (
        "The final prepared package could not be saved and verified."
    ),
    "APPLY_PREPARE_FAILED_SAFELY": (
        "The employer form browser stopped before preparation could be verified. "
        "Check the application status before attempting a safe recovery."
    ),
    "ATS_FORM_PREPARATION_FAILED": (
        "The employer application form could not be prepared or verified. "
        "No submission success is confirmed."
    ),
    "NATIVE_RESUME_PREPARE_FAILED": (
        "Resume generation or its truth and document checks failed. "
        "A new submission has not been confirmed."
    ),
    "ANSWER_RESOLUTION_PREPARE_FAILED": (
        "Application-answer checks could not finish with verified information. "
        "Missing answers must not be guessed."
    ),
    "LEASE_EXPIRED": (
        "A background preparation worker stopped updating its task lease. "
        "The result must be checked before recovery."
    ),
}
_FAILURE_STATUSES = frozenset({"FAILED", "NEEDS_INPUT"})
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")


def safe_failure(tasks: Any) -> tuple[str | None, str | None]:
    """Return a documented code and safe explanation, never raw provider text."""
    if not isinstance(tasks, list):
        return None, None
    for task in tasks:
        if not isinstance(task, Mapping):
            continue
        if str(task.get("status") or "") not in _FAILURE_STATUSES:
            continue
        code = str(task.get("error_code") or "")
        if code in _MESSAGE_BY_CODE:
            return code, _MESSAGE_BY_CODE[code]
        # Codes from unknown providers are not trusted for UI insertion.
        if _CODE_RE.fullmatch(code):
            return code, "Preparation encountered a verification issue. Review the safe recovery options and technical error code."
        return None, "Preparation encountered a verification issue. Review the safe recovery options."
    return None, None


def submission_claim_exists(flow: Mapping[str, Any]) -> bool:
    """Fail closed on any independently recorded provider send/claim evidence."""
    if flow.get("production_receipt"):
        return True
    review = flow.get("internal_integrity_review")
    if isinstance(review, Mapping) and review.get("approval_consumed"):
        return True
    route = flow.get("provider_route")
    if not isinstance(route, Mapping):
        return False
    if route.get("dispatch_claim_exists") or route.get("provider_submitted_at") or route.get("employer_confirmed_at"):
        return True
    return str(route.get("state") or "").upper() in {
        "PROVIDER_SUBMITTED", "EMPLOYER_CONFIRMED", "SUBMISSION_UNVERIFIED",
        "SENT", "SUBMITTED", "VERIFIED",
    }
