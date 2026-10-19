"""Read-only, provider-neutral coverage digest for Native learning readiness.

Public provider claims are DATA, not executable adapters. Observations originate
from explicitly authorized Relay tools and are privacy-minimized. A process
status, field count, or vendor claim never unlocks an ATS submitter.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

REGISTRY = Path(__file__).resolve().parents[1] / "config" / "relay_provider_claims_20261019.json"
MAX_OWNER_OBSERVATIONS = 120_000
_SLUG = re.compile(r"[^a-z0-9]+")


def _key(value: object) -> str:
    return _SLUG.sub("", str(value or "").casefold())


def public_provider_registry() -> dict[str, Any]:
    """Only machine-readable *marketing claims*; never proof of Native support."""
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    if data.get("scope") != "PUBLIC_VENDOR_CLAIM_NOT_NATIVE_SUPPORT":
        raise ValueError("Unexpected provider-claim authority.")
    providers = data.get("providers")
    if not isinstance(providers, list) or len(providers) != 30:
        raise ValueError("Public provider-claim snapshot requires independent review.")
    normalized = [_key(name) for name in providers]
    if len(set(normalized)) != len(normalized) or any(not key for key in normalized):
        raise ValueError("Duplicate or invalid public provider name.")
    return data


def summarize_evidence(
    rows: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Pure summary: never infer accounts, real receipts, or browser capability."""
    canonical = {_key(name): str(name) for name in registry["providers"]}
    aliases = {_key(alias): _key(target) for alias, target
               in dict(registry.get("aliases") or {}).items()}
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "observations": 0, "status_observations": 0,
        "review_observations": 0, "form_fields": 0,
        "field_application_refs": set(), "all_application_refs": set(),
        "terminal_application_refs": set(),
        "required_missing_fields": 0, "unknown_semantic_fields": 0,
        "form_kinds": set(), "latest_observed_at": None,
        "seen_aliases": set(),
    })
    grade_counts: dict[str, int] = defaultdict(int)
    latest_by_app: dict[str, tuple[str, str, bool]] = {}
    total = 0
    all_refs: set[str] = set()
    latest_at: str | None = None
    for row in rows:
        raw = str(row.get("ats_type") or "unknown")[:64].casefold()
        name_key = aliases.get(_key(raw), _key(raw))
        display = canonical.get(name_key)
        if display is None:
            display = "Unclassified" if name_key in {"", "unknown"} else raw
        group = stats[display]
        total += 1
        group["observations"] += 1
        group["seen_aliases"].add(raw)
        observed = str(row.get("observed_at") or "")[:40]
        if observed:
            group["latest_observed_at"] = max(
                observed, group["latest_observed_at"] or observed
            )
            latest_at = max(observed, latest_at or observed)
        grade = str(row.get("evidence_grade") or "")
        grade_counts[grade] += 1
        ref = str(row.get("provider_application_ref_sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", ref):
            continue
        all_refs.add(ref)
        group["all_application_refs"].add(ref)
        kind = str(row.get("observation_kind") or "")
        if kind == "APPLICATION_STATUS":
            group["status_observations"] += 1
            if grade == "OBSERVED":
                try:
                    payload = json.loads(str(row.get("observed_json") or "{}"))
                    is_terminal = payload.get("is_terminal") is True
                except (TypeError, ValueError, AttributeError):
                    is_terminal = False
                event = (observed, str(row.get("observation_id") or ""))
                current = latest_by_app.get(ref)
                if current is None or event > (current[0], current[1]):
                    latest_by_app[ref] = (event[0], event[1], is_terminal)
        elif kind == "APPLICATION_REVIEW":
            group["review_observations"] += 1
        elif kind == "FORM_FIELD" and grade == "OBSERVED":
            group["form_fields"] += 1
            group["field_application_refs"].add(ref)
            if row.get("required") in (True, 1) and row.get("has_value") in (False, 0):
                group["required_missing_fields"] += 1
            if str(row.get("semantic_question_key") or "unknown") == "unknown":
                group["unknown_semantic_fields"] += 1
            field_kind = str(row.get("field_kind") or "unknown")
            if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", field_kind):
                group["form_kinds"].add(field_kind)
    for ref, (_, _, is_terminal) in latest_by_app.items():
        if is_terminal:
            for group in stats.values():
                if ref in group["all_application_refs"]:
                    group["terminal_application_refs"].add(ref)
    results = []
    for name in list(registry["providers"]) + sorted(
        key for key in stats if key not in registry["providers"]
    ):
        group = stats[name]
        unique_fields = len(group["field_application_refs"])
        status_count = group["status_observations"]
        if unique_fields >= 5:
            stage = "FORM_EVIDENCE_READY_FOR_HELD_OUT_NATIVE_REPLAY"
        elif unique_fields:
            stage = "EARLY_FORM_EVIDENCE"
        elif status_count:
            stage = "STATUS_ONLY_NOT_NATIVE_READY"
        else:
            stage = "NO_USABLE_OBSERVATIONS"
        results.append({
            "provider": name,
            "public_vendor_claim": name in registry["providers"],
            "learning_stage": stage,
            "distinct_application_refs": len(group["all_application_refs"]),
            "terminal_process_refs": len(group["terminal_application_refs"]),
            "status_observations": status_count,
            "review_observations": group["review_observations"],
            "form_observations": group["form_fields"],
            "distinct_form_application_refs": unique_fields,
            "required_fields_missing_at_review": group["required_missing_fields"],
            "unknown_question_concepts": group["unknown_semantic_fields"],
            "observed_control_kinds": sorted(group["form_kinds"])[:40],
            "latest_observed_at": group["latest_observed_at"],
            "native_browser_tested": False,
            "native_auto_upgrade_approved": False,
            "employer_receipt_verified_by_this_evidence": False,
            "next_step": (
                "Collect consented field-review evidence or Native synthetic form traces"
                if unique_fields == 0 else
                "Run isolated Native replay with independent expected fields"
            ),
        })
    claimed = [row for row in results if row["public_vendor_claim"]]
    return {
        "version": "munshi-native-provider-learning-digest-v1",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "claim_source": str(registry.get("source_url") or ""),
        "claim_snapshot_as_of": str(registry.get("as_of") or ""),
        "scope": "READ_ONLY_PROVIDER_EVIDENCE_FOR_NATIVE_RESEARCH",
        "public_provider_claims": len(registry["providers"]),
        "claimed_providers_with_status_evidence": sum(
            bool(row["status_observations"]) for row in claimed
        ),
        "claimed_providers_with_form_evidence": sum(
            bool(row["form_observations"]) for row in claimed
        ),
        "native_provider_submitters_validated_by_digest": 0,
        "total_observations": total,
        "distinct_provider_application_refs": len(all_refs),
        "observation_grades": dict(sorted(grade_counts.items())),
        "latest_observed_at": latest_at,
        "unmatched_observed_providers": sum(
            bool(row["status_observations"] or row["form_observations"])
            for row in results if not row["public_vendor_claim"]
        ),
        "providers": results,
        "learned_rules_executable": False,
        "raw_answers_included": False,
        "credentials_or_urls_included": False,
        "independent_receipts_confirmed": False,
        "learning_not_equivalent_to_native_implementation": True,
    }


def provider_learning_digest() -> dict[str, Any]:
    """Tenant-scoped and read-only; no schema installs or external network calls."""
    reg = public_provider_registry()
    connection = get_connection()
    try:
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='tsenta_observations_v1'"
        ).fetchone()
        if table is None:
            return summarize_evidence([], reg)
        owner = current_owner_readonly(connection)
        raw = connection.execute(
            """SELECT observation_id,provider_application_ref_sha256,
                      ats_type,observation_kind,evidence_grade,observed_json,
                      field_kind,required,has_value,semantic_question_key,
                      observed_at
                 FROM tsenta_observations_v1
                WHERE tenant_id=? AND user_id=?
                ORDER BY observed_at ASC,observation_id ASC LIMIT ?""",
            (owner.tenant_id, owner.user_id, MAX_OWNER_OBSERVATIONS + 1),
        ).fetchall()
        if len(raw) > MAX_OWNER_OBSERVATIONS:
            raise RuntimeError(
                "Learning history exceeds safe snapshot limit; paginate before digest."
            )
        return summarize_evidence([dict(row) for row in raw], reg)
    finally:
        connection.close()
