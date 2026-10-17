"""Non-executing validation for a future one-shot Tsenta submission.

Never infer application authority from OAuth, an env flag, or Tsenta's own
auto-submit preferences. A live write path additionally needs the durable,
single-use route claim and MUNSHI's explicit package approval.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from typing import Any

REQUIRED_TOOLS = frozenset({
    "upload_custom_resume",
    "check_custom_resume",
    "apply_with_custom_resume",
})
SHA256 = re.compile(r"[0-9a-f]{64}")
ADDRESS = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _email(value: Any) -> str:
    result = str(value or "").strip()
    if len(result) > 254 or not ADDRESS.fullmatch(result):
        raise ValueError("The MUNSHI managed application email is invalid.")
    return result


def submission_readiness(
    *,
    route: Mapping[str, Any],
    profile: Mapping[str, Any],
    resume_pdf: bytes,
    managed_email: str,
    relay_ready: bool,
    available_tools: frozenset[str],
    profile_verified: bool,
) -> dict[str, Any]:
    """Return non-sensitive blockers; never return email, resume or tokens."""
    reasons: list[str] = []
    if str(route.get("provider") or "") != "TSENTA":
        reasons.append("EXACT_OPENING_OWNED_BY_ANOTHER_ENGINE")
    if str(route.get("state") or "") != "PREPARATION":
        reasons.append("APPLICATION_NOT_AVAILABLE_FOR_FIRST_DISPATCH")
    if route.get("provider_application_id_sha256") or route.get("receipt_method"):
        reasons.append("PRIOR_PROVIDER_OR_RECEIPT_EVIDENCE_EXISTS")
    if not profile_verified:
        reasons.append("TSENTA_ACCOUNT_PROFILE_UNVERIFIED")
    if not relay_ready:
        reasons.append("MUNSHI_MANAGED_INBOX_NOT_READY")
    if not REQUIRED_TOOLS.issubset(available_tools):
        reasons.append("TSENTA_CUSTOM_RESUME_TOOLS_UNAVAILABLE")
    if not isinstance(resume_pdf, bytes) or not resume_pdf.startswith(b"%PDF-") or (
        not 100 <= len(resume_pdf) <= 8_000_000
    ):
        reasons.append("VERIFIED_PDF_ARTIFACT_UNAVAILABLE")
    digest = str(route.get("resume_sha256") or "")
    if not SHA256.fullmatch(digest) or (
        isinstance(resume_pdf, bytes)
        and hashlib.sha256(resume_pdf).hexdigest() != digest
    ):
        reasons.append("RESUME_BYTES_DIFFER_FROM_BOUND_PACKAGE")
    selected = profile.get("data") if isinstance(profile, Mapping) else None
    personal = selected.get("personalInformation") if isinstance(selected, Mapping) else None
    if not isinstance(personal, Mapping) or not (
        str(personal.get("firstName") or "").strip()
        and str(personal.get("lastName") or "").strip()
    ):
        reasons.append("TSENTA_IDENTITY_PROFILE_INCOMPLETE")
    try:
        _email(managed_email)
    except ValueError:
        reasons.append("MUNSHI_MANAGED_EMAIL_INVALID")
    return {
        "ready_for_review": not reasons,
        "blockers": reasons,
        "managed_alias_binding_required": True,
        "requires_one_use_approval_and_claim": True,
        "submit_authorized": False,
        "raw_personal_data_in_output": False,
    }


def tsenta_profile_with_managed_email(
    *, profile: Mapping[str, Any], managed_email: str
) -> dict[str, Any]:
    """Use Tsenta saved resume data but MUNSHI's existing verified inbox.

    Returns an ephemeral deep copy used only with the custom-resume tool.
    Tsenta's saved profile is not silently modified.
    """
    email = _email(managed_email)
    if not isinstance(profile, Mapping):
        raise TypeError("A verified Tsenta profile is required.")
    data = profile.get("data")
    personal = data.get("personalInformation") if isinstance(data, Mapping) else None
    if not isinstance(personal, Mapping):
        raise TypeError("Tsenta's personal-information profile is incomplete.")
    if not (str(personal.get("firstName") or "").strip() and
            str(personal.get("lastName") or "").strip()):
        raise ValueError("A truthful name is required for this application.")
    selected = copy.deepcopy(dict(data))
    selected["personalInformation"]["email"] = email
    return selected
