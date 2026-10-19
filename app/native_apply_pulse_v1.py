"""Evidence-based MUNSHI Native Apply Pulse for job cards.

This is a workflow preparedness index, NOT a calibrated probability of
employer submission or hiring. Marketing claims and Relay completion states
cannot increase a Native success signal. All evidence reads are owner-scoped.
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict
from typing import Any, Mapping

from app.job_destination_v1 import JobDestination, describe_job_destination
from app.native_provider_learning_digest_v1 import provider_learning_digest, public_provider_registry
from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

PULSE_VERSION = "munshi-native-apply-pulse-v1"
KEY = re.compile(r"[^a-z0-9]+")
AGGREGATORS = frozenset(("linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com"))


def slug(text: Any) -> str:
    return KEY.sub("", str(text or "").casefold())


def _native_successes() -> dict[str, int]:
    """Count only independently confirmed Native routes, not Relay statuses.

    The projection includes employer job URLs for classifier use only. Nothing
    sensitive is returned to job cards, cached, or persisted by this module.
    """
    connection = get_connection()
    try:
        connection.execute("PRAGMA query_only=ON")
        needed = ("application_provider_routes_v1", "jobs")
        if any(connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is None for name in needed):
            return {}
        owner = current_owner_readonly(connection)
        rows = connection.execute(
            """SELECT j.source, j.job_url, j.apply_url, j.detail_extraction_json
                 FROM application_provider_routes_v1 r
                 JOIN jobs j ON j.id=r.job_id
                WHERE r.tenant_id=? AND r.user_id=?
                  AND r.provider='NATIVE' AND r.state='EMPLOYER_CONFIRMED'
                  AND r.receipt_evidence_digest IS NOT NULL
                  AND trim(r.receipt_evidence_digest)!=''
                ORDER BY r.updated_at DESC LIMIT 2000""",
            (owner.tenant_id, owner.user_id),
        ).fetchall()
        totals: dict[str, int] = defaultdict(int)
        for row in rows:
            provider = describe_job_destination(dict(row)).provider
            if provider:
                totals[slug(provider)] += 1
        return dict(totals)
    finally:
        connection.close()


def evidence_snapshot() -> dict[str, Any]:
    """A single, bounded, fail-closed snapshot for each rendered job grid."""
    digest = provider_learning_digest()
    registry = public_provider_registry()
    aliases = {slug(name): slug(name) for name in registry["providers"]}
    aliases.update({
        slug(raw): slug(canonical)
        for raw, canonical in dict(registry.get("aliases") or {}).items()
    })
    # These are nomenclature aliases, not company-specific or ATS selectors.
    aliases.update({
        "successfactors": "sapsuccessfactors",
        "ukg": "ultipro",
    })
    by_slug = {slug(row["provider"]): row for row in digest["providers"]}
    return {
        "version": PULSE_VERSION,
        "providers": by_slug,
        "aliases": aliases,
        "native_receipts": _native_successes(),
        "latest_learning": digest.get("latest_observed_at"),
    }


def _provider_evidence(destination: JobDestination, snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    key = slug(getattr(destination, "provider", None))
    key = dict(snapshot.get("aliases") or {}).get(key, key)
    item = dict(snapshot.get("providers") or {}).get(key) or {}
    receipts = dict(snapshot.get("native_receipts") or {})
    raw_native = int(receipts.get(key, 0) or 0)
    if key == "sapsuccessfactors":
        raw_native += int(receipts.get("successfactors", 0) or 0)
    if key == "ultipro":
        raw_native += int(receipts.get("ukg", 0) or 0)
    return dict(item), max(0, raw_native)


def apply_pulse(
    destination: JobDestination, evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministic index: destination + ATS + learning + Native receipts.

    These are additive evidence milestones, NOT empirical submission odds.
    Missing training does not create a fabricated success history.
    """
    host = str(getattr(destination, "destination_domain", "") or "").casefold().rstrip(".")
    direct = (
        bool(getattr(destination, "original_url", None))
        and bool(host)
        and not any(host == agg or host.endswith("." + agg) for agg in AGGREGATORS)
        and getattr(destination, "confidence", None) in {"high", "stored", "domain_only"}
    )
    if not direct:
        return {
            "version": PULSE_VERSION, "percent": None, "stage": "Gathering",
            "label": "MUNSHI Apply Pulse", "native_receipts": 0,
            "description": "Employer link being resolved",
            "data_ready": False,
            "is_submission_probability": False,
        }
    stats, native_count = _provider_evidence(destination, evidence)
    raw_ref_count = max(0, int(stats.get("distinct_application_refs") or 0))
    form_refs = max(0, int(stats.get("distinct_form_application_refs") or 0))
    kinds = list(stats.get("observed_control_kinds") or [])
    recognized = bool(getattr(destination, "provider", None))
    # 34pts generic built-in preparation + 18pts exact employer route.
    # Platform type, history, actual fields and independently verified Native
    # receipts add capped evidence. Vendor marketing has *zero* weight.
    score = 34 + 18
    if recognized:
        score += 10
    if raw_ref_count:
        score += round(8 * min(1.0, math.log1p(raw_ref_count) / math.log1p(24)))
    if form_refs:
        score += round(13 * min(1.0, form_refs / 6))
        score += round(5 * min(1.0, len(kinds) / 4))
    if native_count:
        score += round(12 * min(1.0, native_count / 3))
    percent = min(96 if native_count else 86, score)
    if native_count:
        stage = "Live-tested"
    elif form_refs >= 5:
        stage = "Well practiced"
    elif raw_ref_count or form_refs:
        stage = "Learning"
    else:
        stage = "Exploring"
    return {
        "version": PULSE_VERSION, "percent": int(percent),
        "stage": stage, "label": "MUNSHI Apply Pulse",
        "native_receipts": native_count,
        "description": (
            "Native workflow readiness from employer link and saved "
            "ATS/form evidence; not a guarantee of submission."
        ),
        "data_ready": bool(raw_ref_count or form_refs or native_count),
        "is_submission_probability": False,
    }
