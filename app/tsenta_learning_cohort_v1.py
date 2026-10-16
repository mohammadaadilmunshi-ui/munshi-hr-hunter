"""Provider-neutral, privacy-minimized Tsenta -> MUNSHI Native learning cohorts.

A 'processed application' is one unique provider application with a terminal,
observed status. Neither form-field counts nor provider-submitted status imply
an employer receipt. Shadow improvement candidates are never executable rules.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

VERSION = "munshi-provider-learning-cohort-v1"
COHORT_MIN = 10
COHORT_TARGET = 15
MAX_STATUS_EVENTS = 50000
MAX_COHORT_FIELDS = COHORT_TARGET * 500


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure aggregator: dynamic ATS families, no URLs, profile data or raw IDs.

    Rows originate only from the sanitized observations projection. A missing
    or non-terminal status never contributes toward a promotion cohort.
    """
    latest: dict[str, dict[str, Any]] = {}
    fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        reference = str(row.get("provider_application_ref_sha256") or "")
        if len(reference) != 64 or any(c not in "0123456789abcdef" for c in reference):
            continue
        kind = str(row.get("observation_kind") or "")
        if row.get("evidence_grade") != "OBSERVED":
            continue
        if kind == "FORM_FIELD":
            fields[reference].append(row)
        if kind != "APPLICATION_STATUS":
            continue
        try:
            signal = json.loads(str(row.get("observed_json") or "{}"))
        except (TypeError, ValueError):
            continue
        # Only real, observed terminal process outcomes qualify; a provider
        # can report submitted without offering a true employer receipt.
        when = (str(row.get("observed_at") or ""), str(row.get("observation_id") or ""))
        if reference not in latest or when > latest[reference]["when"]:
            latest[reference] = {
                "when": when,
                "ats": str(row.get("ats_type") or "unknown").lower()[:64],
                "terminal": signal.get("is_terminal") is True,
                "reported_status": str(signal.get("provider_status") or "UNKNOWN")[:64],
            }
    terminal = sorted(
        ((ref, detail) for ref, detail in latest.items() if detail["terminal"]),
        key=lambda item: (item[1]["when"], item[0]),
    )
    count = len(terminal)
    start = (count // COHORT_TARGET) * COHORT_TARGET
    window = terminal[start:]
    if count >= COHORT_TARGET and count % COHORT_TARGET == 0:
        start = count - COHORT_TARGET
        window = terminal[start:]
    cohort_size = len(window)
    ats_counts: dict[str, int] = {}
    field_apps = 0
    candidates: dict[tuple[str, str], set[str]] = defaultdict(set)
    missing: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ref, item in window:
        ats = item["ats"]
        ats_counts[ats] = ats_counts.get(ats, 0) + 1
        observed_fields = fields.get(ref, [])
        if observed_fields:
            field_apps += 1
        seen: set[tuple[str, str]] = set()
        for field in observed_fields:
            fingerprint = str(field.get("field_fingerprint") or "")
            if len(fingerprint) != 64:
                continue
            pair = (ats, fingerprint)
            if pair in seen:
                continue
            seen.add(pair)
            if field.get("required") not in (True, 1):
                continue
            candidates[pair].add(ref)
            if field.get("has_value") in (False, 0):
                missing[pair].add(ref)
    shadow = []
    for (ats, fingerprint), references in sorted(candidates.items()):
        total = len(references)
        missing_count = len(missing.get((ats, fingerprint), set()))
        if total < 3 or missing_count < 2:
            continue
        shadow.append({
            "ats_type": ats,
            "field_fingerprint": fingerprint,
            "unique_application_support": total,
            "missing_at_review_count": missing_count,
            "hypothesis": "Prioritize Native field detection and candidate-truth lookup",
            "deployment_state": "SHADOW_ONLY",
            "verified_native_lift": False,
        })
    shadow.sort(
        key=lambda x: (-x["missing_at_review_count"], -x["unique_application_support"],
                       x["ats_type"], x["field_fingerprint"])
    )
    phase = (
        "COLLECTING" if cohort_size < COHORT_MIN else
        "EVALUATION_READY" if cohort_size < COHORT_TARGET else "COHORT_COMPLETE"
    )
    # Shadow candidates must be evaluated on separate Native browser replay
    # and never authorize answer fabrication or automatic submissions.
    return {
        "version": VERSION,
        "processed_applications_total": count,
        "cohort_number": (start // COHORT_TARGET) + 1,
        "cohort_processed": cohort_size,
        "cohort_minimum": COHORT_MIN,
        "cohort_target": COHORT_TARGET,
        "phase": phase,
        "unique_ats_in_cohort": len(ats_counts),
        "ats_coverage": ats_counts,
        "review_observed_applications": field_apps,
        "shadow_improvement_candidates": shadow[:20] if phase != "COLLECTING" else [],
        "auto_upgrade_enabled": False,
        "independent_employer_receipts_verified": False,
        "answers_or_applicant_data_included": False,
        "platform_strategy": "DYNAMIC_ATS_ADAPTERS",
    }


def learning_cohort_snapshot() -> dict[str, Any]:
    """Read-only owner-scoped snapshot; never install schemas or fetch Tsenta."""
    connection = get_connection()
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='tsenta_observations_v1'"
        ).fetchone()
        if exists is None:
            return summarize_rows([])
        owner = current_owner_readonly(connection)
        # Read status evidence independently from field-volume telemetry.
        # A busy account must not stop learning merely because each
        # application has hundreds of sanitized form controls.
        raw_statuses = connection.execute(
            """SELECT observation_id,provider_application_ref_sha256,ats_type,
                      observation_kind,evidence_grade,observed_json,observed_at,
                      field_fingerprint,required,has_value
                 FROM tsenta_observations_v1
                WHERE tenant_id=? AND user_id=?
                  AND observation_kind='APPLICATION_STATUS'
                ORDER BY observed_at ASC, observation_id ASC LIMIT ?""",
            (owner.tenant_id, owner.user_id, MAX_STATUS_EVENTS + 1),
        ).fetchall()
        if len(raw_statuses) > MAX_STATUS_EVENTS:
            result = summarize_rows([])
            result["phase"] = "STATUS_HISTORY_LIMIT_REQUIRES_PAGINATION"
            result["observation_limit_reached"] = True
            return result
        statuses = [dict(row) for row in raw_statuses]
        latest: dict[str, dict[str, Any]] = {}
        for row in statuses:
            if row.get("evidence_grade") != "OBSERVED":
                continue
            ref = str(row.get("provider_application_ref_sha256") or "")
            try:
                parsed = json.loads(str(row["observed_json"] or "{}"))
            except (TypeError, ValueError):
                continue
            ordering = (str(row["observed_at"]), str(row["observation_id"]))
            if ref not in latest or ordering > latest[ref]["ordering"]:
                latest[ref] = {
                    "ordering": ordering,
                    "terminal": parsed.get("is_terminal") is True,
                }
        terminal = sorted(
            ((ref, item) for ref, item in latest.items() if item["terminal"]),
            key=lambda entry: (entry[1]["ordering"], entry[0]),
        )
        total = len(terminal)
        start = (total // COHORT_TARGET) * COHORT_TARGET
        if total and total % COHORT_TARGET == 0:
            start -= COHORT_TARGET
        recent_refs = [ref for ref, _ in terminal[start:]]
        form_rows: list[dict[str, Any]] = []
        if recent_refs:
            placeholders = ",".join("?" for _ in recent_refs)
            query = (
                """SELECT observation_id,provider_application_ref_sha256,ats_type,
                          observation_kind,evidence_grade,observed_json,observed_at,
                          field_fingerprint,required,has_value
                     FROM tsenta_observations_v1
                    WHERE tenant_id=? AND user_id=?
                      AND observation_kind='FORM_FIELD'
                      AND provider_application_ref_sha256 IN ("""
                + placeholders +
                """)
                    ORDER BY observed_at ASC,observation_id ASC LIMIT ?"""
            )
            raw_fields = connection.execute(
                query, (owner.tenant_id, owner.user_id,
                        *recent_refs, MAX_COHORT_FIELDS + 1),
            ).fetchall()
            if len(raw_fields) > MAX_COHORT_FIELDS:
                result = summarize_rows(statuses)
                result["phase"] = "COHORT_FIELD_LIMIT_REQUIRES_PAGINATION"
                result["observation_limit_reached"] = True
                result["shadow_improvement_candidates"] = []
                return result
            form_rows = [dict(row) for row in raw_fields]
        return summarize_rows(statuses + form_rows)
    finally:
        connection.close()
