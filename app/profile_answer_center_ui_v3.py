"""Truth-safe compatibility layer for the runtime-safe Answer Center.

The legacy encrypted profile has one generic ``sponsorship_required`` boolean,
while the semantic catalog intentionally distinguishes sponsorship *now* from
sponsorship *in the future*. One generic value must not make both meanings look
configured. Until Candidate Truth stores those two answers independently, V3
leaves both sponsorship concepts pending/contextual instead of overstating
protected readiness.
"""
from __future__ import annotations

from typing import Any, Mapping

from app import profile_answer_center_ui_v2 as v2


_TRUTH_SAFE_PROTECTED_DETAIL_FIELDS = {
    "eligibility.work_authorized_us": "authorized_to_work",
    "eligibility.visa_status": "visa_or_permit",
    "self_id.veteran": "veteran",
    "self_id.disability": "disability",
    "self_id.gender": "gender",
    "self_id.race_ethnicity": "ethnicity",
}


def _install_truth_safe_policy() -> None:
    # V2 owns the complete UI/runtime implementation. Override only its internal
    # readiness projection with the narrower mapping before each public entry.
    # This does not delete the legacy encrypted sponsorship value; it simply
    # refuses to claim that one value proves two distinct semantic answers.
    v2._PROTECTED_DETAIL_FIELDS.clear()
    v2._PROTECTED_DETAIL_FIELDS.update(_TRUTH_SAFE_PROTECTED_DETAIL_FIELDS)


def coverage_breakdown(readiness: Mapping[str, Any]) -> dict[str, int]:
    _install_truth_safe_policy()
    return v2.coverage_breakdown(readiness)


def render(authority: dict[str, Any]) -> None:
    _install_truth_safe_policy()
    v2.render(authority)
