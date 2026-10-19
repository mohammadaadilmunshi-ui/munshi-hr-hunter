"""Every advertised provider is tracked, never mistaken for Native support."""
from __future__ import annotations

import json

from app.native_provider_learning_digest_v1 import (
    provider_learning_digest, public_provider_registry, summarize_evidence,
)


def _row(name: str, ref: str, kind: str, *, evidence: str = "OBSERVED",
         required: int | None = None, has_value: int | None = None,
         semantic: str = "unknown") -> dict:
    return {
        "observation_id": f"synthetic-{ref}-{kind}-{name}",
        "provider_application_ref_sha256": ref * 64,
        "ats_type": name, "observation_kind": kind, "evidence_grade": evidence,
        "observed_json": json.dumps({
            "is_terminal": True, "provider_status": "COMPLETED",
            "answer_value_recorded": False,
        }),
        "field_kind": "text", "required": required, "has_value": has_value,
        "semantic_question_key": semantic, "observed_at": "2026-10-19T12:00:00+00:00",
    }


def test_all_thirty_public_claims_and_dynamic_unknown_providers():
    reg = public_provider_registry()
    assert len(reg["providers"]) == 30
    assert "Workday" in reg["providers"]
    assert "SAP SuccessFactors" in reg["providers"]
    assert "Teamtailor" in reg["providers"]
    snap = summarize_evidence([], reg)
    assert len(snap["providers"]) == 30
    assert snap["native_provider_submitters_validated_by_digest"] == 0
    assert all(row["learning_stage"] == "NO_USABLE_OBSERVATIONS"
               for row in snap["providers"])
    assert snap["learned_rules_executable"] is False


def test_one_provider_alias_does_not_inflate_support():
    reg = public_provider_registry()
    rows = [
        _row("successfactors", "a", "APPLICATION_STATUS"),
        _row("sap-successfactors", "b", "FORM_FIELD",
             required=1, has_value=0),
        _row("future-ats-x", "c", "APPLICATION_STATUS"),
    ]
    snap = summarize_evidence(rows, reg)
    platform = next(p for p in snap["providers"] if p["provider"] == "SAP SuccessFactors")
    assert platform["status_observations"] == 1
    assert platform["form_observations"] == 1
    assert platform["distinct_form_application_refs"] == 1
    assert platform["required_fields_missing_at_review"] == 1
    assert platform["learning_stage"] == "EARLY_FORM_EVIDENCE"
    assert platform["native_browser_tested"] is False
    assert snap["unmatched_observed_providers"] == 1
    assert next(p for p in snap["providers"] if p["provider"] == "future-ats-x")[
        "public_vendor_claim"
    ] is False


def test_status_only_never_becomes_an_executable_native_rule():
    reg = public_provider_registry()
    rows = [
        _row("workday", format(i, "x"), "APPLICATION_STATUS") for i in range(12)
    ]
    digest = summarize_evidence(rows, reg)
    result = next(p for p in digest["providers"] if p["provider"] == "Workday")
    assert result["learning_stage"] == "STATUS_ONLY_NOT_NATIVE_READY"
    assert result["terminal_process_refs"] == 12
    assert result["native_auto_upgrade_approved"] is False
    assert result["employer_receipt_verified_by_this_evidence"] is False


def test_no_applicant_values_urls_or_tokens_ever_leave_digest():
    reg = public_provider_registry()
    row = _row("dayforce", "c", "FORM_FIELD", required=1, has_value=0)
    row["observed_json"] = json.dumps({
        "value": "private.user@example.com",
        "token": "private-access-key",
        "url": "https://employer.example/apply",
    })
    digest = summarize_evidence([row], reg)
    serialized = json.dumps(digest)
    assert "private.user" not in serialized
    assert "private-access-key" not in serialized
    assert "employer.example" not in serialized
    assert "c" * 64 not in serialized
    assert digest["raw_answers_included"] is False


def test_hunter_database_digest_is_read_only(hunter_db):
    del hunter_db
    from app.database import get_connection
    with get_connection() as con:
        before = con.execute("PRAGMA schema_version").fetchone()[0]
    snap = provider_learning_digest()
    with get_connection() as con:
        after = con.execute("PRAGMA schema_version").fetchone()[0]
    assert before == after
    assert snap["public_provider_claims"] == 30
    assert snap["total_observations"] == 0
