from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pandas as pd

from app.ui_time import local_date, parse_timestamp, system_timezone


DEFAULT_N8N_DB = Path.home() / ".n8n" / "database.sqlite"

REASON_LABELS = {
    "ELIGIBLE": "Eligible",
    "DUPLICATE": "Already discovered",
    "REJECT_COMPANY": "Company exclusion",
    "REJECT_LOCATION": "Location not eligible",
    "REJECT_ROLE": "Role outside targeting",
    "REJECT_HARD_REQUIREMENT": "Hard requirement",
    "REJECT_OTHER_TARGETING": "Other targeting rule",
    "company_blacklisted": "Company exclusion",
    "company_exclusion": "Company exclusion",
    "configured_role_evidence": "Target role matched",
    "configured_target_role": "Job title",
    "confirmed_us_eligible": "United States eligibility confirmed",
    "hard_requirement": "Hard requirement",
    "quarantined_before_telegram": "Not delivered because targeting rejected the opportunity",
    "title_not_in_configured_role_families": "Role outside targeting",
    "foreign_only_location": "Location not eligible",
    "unknown_country": "Location eligibility not confirmed",
    "explicit_non_us_country": "Outside United States targeting",
    "country_unknown_fail_closed": "United States eligibility not confirmed",
    "canonical_targeting_match": "Canonical targeting matched",
    "configured_hard_requirement": "Configured hard requirement",
    "duplicate_within_run": "Already discovered in this source run",
    "explicit_foreign_country_text": "Foreign location detected",
    "source_provenance": "Source history",
    "duplicate_group": "Duplicate identity group",
    "job_fingerprint": "Record fingerprint",
    "description_raw": "Job description",
    "title": "Job title",
    "onsite": "Onsite",
    "on-site": "Onsite",
    "hybrid": "Hybrid",
    "remote": "Remote",
    "required": "Required",
    "preferred": "Preferred",
    "unknown": "Not classified",
}

COUNTRY_LABELS = {
    "US": "United States",
    "USA": "United States",
    "UNITED STATES": "United States",
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "UNITED KINGDOM": "United Kingdom",
    "CA": "Canada",
    "CANADA": "Canada",
    "PL": "Poland",
    "POLAND": "Poland",
    "DE": "Germany",
    "GERMANY": "Germany",
    "FR": "France",
    "FRANCE": "France",
    "IN": "India",
    "INDIA": "India",
    "AU": "Australia",
    "AUSTRALIA": "Australia",
}

US_STATE_LABELS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def humanize_machine_value(value: Any, *, empty: str = "Not available") -> str:
    text = str(value or "").strip()
    if not text or text.casefold() in {"none", "null", "nan", "nat"}:
        return empty
    if text in REASON_LABELS:
        return REASON_LABELS[text]
    upper = text.upper()
    if upper in REASON_LABELS:
        return REASON_LABELS[upper]
    words = text.replace("_", " ").strip().lower().split()
    acronyms = {"hr": "HR", "us": "U.S.", "usa": "USA", "api": "API", "ats": "ATS", "n8n": "n8n"}
    return " ".join(acronyms.get(word, word.capitalize()) for word in words)


def _decoded(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _record_value(record: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        value = record[key]
    except (KeyError, TypeError, IndexError):
        value = record.get(key, default) if hasattr(record, "get") else default
    return default if value is None else value


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value.strip().casefold() not in {"none", "null", "nan", "nat"}
    return True


def _country_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not supplied by provider"
    return COUNTRY_LABELS.get(text.upper(), humanize_machine_value(text))


def _state_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Not specified"
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return US_STATE_LABELS.get(text.upper(), humanize_machine_value(text))


def _year_label(value: Any, *, empty: str = "Not specified") -> str:
    try:
        years = float(value)
    except (TypeError, ValueError):
        return empty
    if years.is_integer():
        integer = int(years)
        return f"{integer} year" if integer == 1 else f"{integer} years"
    return f"{years:g} years"


def _field(value: Any, empty: str = "Not available") -> str:
    return humanize_machine_value(value, empty=empty)


def _match_source_label(role: Mapping[str, Any]) -> str:
    evidence = role.get("evidence") or []
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, Mapping) and _present(item.get("field")):
                return _field(item.get("field"))
    raw = role.get("match_source")
    if str(role.get("reason") or "") == "configured_role_evidence" and raw == role.get("target_family"):
        return "Job title"
    return _field(raw)


def explainable_evidence_model(
    record: Mapping[str, Any] | Any,
    *,
    rejected_record: bool = False,
) -> dict[str, Any]:
    """Translate canonical machine evidence into a presentation-safe explanation."""
    evidence_field = "evidence_json" if rejected_record else "decision_evidence_json"
    evidence = dict(_decoded(_record_value(record, evidence_field), {}) or {})
    role = dict(evidence.get("role") or _decoded(_record_value(record, "role_evidence_json"), {}) or {})
    location = dict(evidence.get("location") or _decoded(_record_value(record, "location_evidence_json"), {}) or {})
    location_detail = dict(location.get("evidence") or {})
    hard_requirement = dict(evidence.get("hard_requirement") or {})
    experience = list(
        evidence.get("experience")
        or hard_requirement.get("all_experience_evidence")
        or _decoded(_record_value(record, "experience_evidence_json"), [])
        or []
    )
    configured_matches = list(hard_requirement.get("configured_phrase_matches") or [])
    primary_code = str(
        _record_value(record, "primary_category" if rejected_record else "primary_decision", "LEGACY")
        or "LEGACY"
    ).upper()
    is_eligible = primary_code == "ELIGIBLE"
    role_passed = bool(role.get("accepted"))
    location_passed = bool(location.get("accepted"))
    hard_rejected = bool(hard_requirement.get("rejected")) or primary_code == "REJECT_HARD_REQUIREMENT"
    target_family = role.get("target_family") or _record_value(record, "target_track")
    overall_reason = evidence.get("reason") or primary_code

    reported_location = (
        location_detail.get("location_raw")
        or _record_value(record, "location_raw")
        or "Not supplied by provider"
    )
    raw_country = (
        location_detail.get("raw_country")
        or location_detail.get("provider_country")
        or _record_value(record, "country")
    )
    state_evidence = list(location_detail.get("state_evidence") or [])
    city_hints = list(location_detail.get("us_city_hints") or [])
    record_state = _record_value(record, "state")
    record_city = _record_value(record, "city")
    location_parts = [part.strip() for part in str(reported_location).split(",") if part.strip()]
    inferred_city = record_city or (location_parts[0] if len(location_parts) >= 2 else None)
    inferred_state = record_state or (state_evidence[0] if state_evidence else None)
    if not inferred_state and len(location_parts) >= 2:
        possible_state = location_parts[-2] if location_parts[-1].upper() in COUNTRY_LABELS else location_parts[-1]
        inferred_state = possible_state
    if not inferred_city and city_hints:
        inferred_city = city_hints[0]

    foreign_terms = []
    for key in ("foreign_country_terms", "foreign_city_hints", "global_terms", "parenthesized_foreign_codes"):
        foreign_terms.extend(str(item) for item in (location_detail.get(key) or []) if _present(item))
    foreign_display = ", ".join(dict.fromkeys(_field(item) for item in foreign_terms)) or "None detected"
    arrangement = location_detail.get("arrangement") or _record_value(record, "remote_type")

    experience_rows: list[dict[str, str]] = []
    for item in experience:
        if not isinstance(item, Mapping):
            continue
        classification = str(item.get("classification") or "UNKNOWN").upper()
        experience_rows.append(
            {
                "Requirement": str(item.get("evidence") or "Experience requirement detected"),
                "Minimum": _year_label(item.get("minimum_years")),
                "Maximum": _year_label(item.get("maximum_years")),
                "Classification": _field(classification),
                "Impact": "Triggered a hard rejection" if hard_rejected else "Did not trigger a hard rejection",
                "Source": _field(item.get("field"), "Job description"),
            }
        )

    hard_rule_rows = [
        {
            "Rule": _field(item.get("configured_term")),
            "Matched in": _field(item.get("field")),
            "Evidence": str(item.get("evidence") or "Configured rule match"),
            "Impact": "Triggered a hard rejection" if hard_rejected else "Recorded as supporting evidence",
        }
        for item in configured_matches
        if isinstance(item, Mapping)
    ]

    provenance = list(_decoded(_record_value(record, "source_provenance_json"), []) or [])
    if not provenance:
        provenance = [
            {
                "source": _record_value(record, "source_name" if rejected_record else "source", "Unknown source"),
                "external_id": _record_value(record, "external_id" if rejected_record else "ats_job_id", ""),
                "url": "",
            }
        ]
    source_names = list(
        dict.fromkeys(
            str(item.get("source") or "Unknown source")
            for item in provenance
            if isinstance(item, Mapping)
        )
    )
    source_links = [
        {
            "label": f"Open {str(item.get('source') or 'source')} listing",
            "url": str(item.get("url") or ""),
        }
        for item in provenance
        if isinstance(item, Mapping) and str(item.get("url") or "").strip()
    ]
    external_ids = [
        str(item.get("external_id") or "").strip()
        for item in provenance
        if isinstance(item, Mapping) and str(item.get("external_id") or "").strip()
    ]
    provider_id = _record_value(record, "external_id" if rejected_record else "ats_job_id")
    if not _present(provider_id) and external_ids:
        provider_id = external_ids[0]
    fingerprint = _record_value(record, "job_identity" if rejected_record else "job_fingerprint")
    duplicate_group = _record_value(record, "duplicate_group")

    telegram_sent = int(_record_value(record, "telegram_sent", 0) or 0) == 1
    sent_to_n8n = int(_record_value(record, "sent_to_n8n", 0) or 0) == 1
    secondary_reasons = list(_decoded(_record_value(record, "secondary_reasons_json"), []) or [])
    secondary_display = [humanize_machine_value(item) for item in secondary_reasons if _present(item)]
    rule_version = _record_value(record, "rules_version" if rejected_record else "targeting_rules_version")

    if primary_code == "DUPLICATE":
        duplicate_status = "Already observed"
    elif len(source_names) > 1:
        duplicate_status = f"Canonical record observed from {len(source_names)} sources"
    else:
        duplicate_status = "Unique record"

    if is_eligible and telegram_sent:
        delivery_reason = "Opportunity passed targeting and entered Telegram delivery."
    elif is_eligible:
        delivery_reason = "Eligible opportunity is waiting for or did not require this delivery stage."
    else:
        delivery_reason = "Opportunity did not pass canonical targeting."

    return {
        "targeting": [
            ("Overall result", _field(overall_reason)),
            ("Role evaluation", "Passed" if role_passed else "Did not pass"),
            ("Matched role", str(role.get("matched_phrase") or "No configured target role matched")),
            ("Matched from", _match_source_label(role)),
            ("Target family", _field(target_family, "Not classified")),
            ("Canonical role rule", _field(role.get("reason"))),
            ("Experience evidence", f"{len(experience)} requirement{'s' if len(experience) != 1 else ''} detected" if experience else "No experience requirement detected"),
            ("Evidence source", "Configuration-backed rule evidence"),
        ],
        "location": [
            ("Eligibility", "United States eligibility confirmed" if location_passed else "United States eligibility not confirmed"),
            ("Reported location", str(reported_location)),
            ("Country", _country_label(raw_country)),
            ("State", _state_label(inferred_state)),
            ("City", _field(inferred_city, "Not specified")),
            ("Work arrangement", _field(arrangement, "Not specified")),
            ("Provider country", _country_label(location_detail.get("provider_country"))),
            ("Foreign-location indicators", foreign_display),
            (
                "Location decision",
                "Eligible under current nationwide U.S. policy"
                if location_passed
                else f"Not eligible under current nationwide U.S. policy · {_field(location.get('reason'))}",
            ),
        ],
        "experience": [
            ("Requirement detected", "Yes" if experience else "No"),
            ("Targeting impact", "Triggered a hard rejection" if hard_rejected else "Did not trigger a hard rejection"),
            ("Evidence source", "Job description" if experience else "No experience evidence recorded"),
        ],
        "experience_rows": experience_rows,
        "hard_rule_rows": hard_rule_rows,
        "provenance": [
            ("Duplicate status", duplicate_status),
            ("Original source", source_names[0] if source_names else "Not available"),
            ("Provider job ID", str(provider_id) if _present(provider_id) else "Not supplied by provider"),
            ("Canonical source count", str(len(source_names))),
            ("Fingerprint verification", "Available" if _present(fingerprint) or _present(duplicate_group) else "Not available"),
        ],
        "source_names": source_names,
        "source_links": source_links,
        "delivery": [
            ("Telegram", "Sent" if telegram_sent else "Not sent"),
            ("n8n", "Dispatched" if sent_to_n8n else "Not dispatched"),
            ("Why", delivery_reason),
            ("Targeting rule version", f"Version {rule_version}" if _present(rule_version) else "Not available"),
            ("Additional reasons", ", ".join(secondary_display) if secondary_display else "No secondary reasons"),
        ],
        "source": source_names[0] if source_names else "Not available",
        "application_url": str(_record_value(record, "apply_url") or _record_value(record, "job_url") or ""),
    }


def decision_view_model(record: Mapping[str, Any] | Any, *, rejected_record: bool = False) -> dict[str, Any]:
    evidence_field = "evidence_json" if rejected_record else "decision_evidence_json"
    evidence = _decoded(_record_value(record, evidence_field), {})
    role = dict(evidence.get("role") or _decoded(_record_value(record, "role_evidence_json"), {}))
    location = dict(evidence.get("location") or _decoded(_record_value(record, "location_evidence_json"), {}))
    experience = list(evidence.get("experience") or _decoded(_record_value(record, "experience_evidence_json"), []))
    hard_requirement = dict(evidence.get("hard_requirement") or {})
    location_detail = dict(location.get("evidence") or {})

    primary_code = _record_value(
        record,
        "primary_category" if rejected_record else "primary_decision",
        "LEGACY",
    )
    decision = "Eligible" if str(primary_code).upper() == "ELIGIBLE" else "Rejected"
    if str(primary_code).upper() in {"", "LEGACY", "NONE"}:
        decision = "Historical record"

    role_matched = bool(role.get("accepted"))
    location_confirmed = bool(location.get("accepted"))
    hard_rejected = bool(hard_requirement.get("rejected")) or str(primary_code).upper() == "REJECT_HARD_REQUIREMENT"
    target_family = role.get("target_family") or _record_value(record, "target_track", "Not classified")
    arrangement = location_detail.get("arrangement") or _record_value(record, "remote_type", "Not recorded")
    delivered = int(_record_value(record, "telegram_sent", 0) or 0) == 1
    downstream = int(_record_value(record, "sent_to_n8n", 0) or 0) == 1

    required_experience = [
        item for item in experience
        if isinstance(item, Mapping) and str(item.get("classification") or "").upper() == "REQUIRED"
    ]
    first_required = required_experience[0] if required_experience else None
    if first_required and _present(first_required.get("minimum_years")):
        experience_summary = f"{_year_label(first_required.get('minimum_years')).capitalize()} of required experience detected"
    elif experience:
        experience_summary = f"{len(experience)} experience requirement{'s' if len(experience) != 1 else ''} detected"
    elif hard_rejected:
        experience_summary = "A configured hard requirement was detected"
    else:
        experience_summary = "No disqualifying experience requirement detected"

    if delivered:
        delivery_state = "Delivered to Telegram"
        delivery_reason = "Opportunity passed targeting and entered the delivery workflow."
    elif decision == "Rejected":
        delivery_state = "Not delivered"
        delivery_reason = "The opportunity did not pass canonical targeting."
    else:
        delivery_state = "Waiting for delivery"
        delivery_reason = "No completed Telegram delivery is recorded."

    return {
        "decision": decision,
        "primary_reason": (
            "Passed canonical targeting"
            if str(primary_code).upper() == "ELIGIBLE"
            else humanize_machine_value(primary_code)
        ),
        "role_matched": role_matched,
        "role_summary": "Target role matched" if role_matched else "No target role match",
        "matched_phrase": role.get("matched_phrase") or _record_value(record, "title", "Not available"),
        "target_family": humanize_machine_value(target_family),
        "matched_from": _match_source_label(role),
        "location_confirmed": location_confirmed,
        "location_summary": humanize_machine_value(location.get("reason")),
        "location": _record_value(record, "location_raw", "Not available"),
        "arrangement": humanize_machine_value(arrangement),
        "experience_clear": not hard_rejected,
        "experience_summary": experience_summary,
        "experience_impact": (
            "Triggered a configured hard-reject rule"
            if hard_rejected
            else "No configured hard-reject threshold triggered"
        ),
        "experience_evidence_count": len(experience),
        "delivery_state": delivery_state,
        "delivery_reason": delivery_reason,
        "downstream_state": "Dispatched downstream" if downstream else "Not dispatched downstream",
        "source": _record_value(record, "source_name" if rejected_record else "source", "Not available"),
        "application_url": str(_record_value(record, "apply_url") or _record_value(record, "job_url") or ""),
    }


def lifetime_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    source = connection.execute(
        """
        SELECT COUNT(*) runs, MIN(started_at) first_run,
               MAX(COALESCE(completed_at,started_at)) latest_run,
               COALESCE(SUM(request_count),0) requests,
               COALESCE(SUM(raw_count),0) scanned,
               COALESCE(SUM(normalized_count),0) normalized,
               COALESCE(SUM(duplicate_count),0) duplicates,
               COALESCE(SUM(reject_role_count+reject_location_count+
                            reject_hard_requirement_count+reject_company_count+
                            reject_other_targeting_count),0) rejected,
               COALESCE(SUM(eligible_count),0) eligible,
               COALESCE(SUM(new_eligible_count),0) new_eligible,
               COALESCE(SUM(telegram_count),0) source_run_telegram,
               COALESCE(SUM(downstream_success_count),0) downstream,
               COALESCE(SUM(error_count),0) errors,
               COUNT(DISTINCT source_name) providers_run
        FROM source_runs
        """
    ).fetchone()
    jobs = connection.execute(
        """
        SELECT COUNT(*) jobs_stored, MIN(first_seen_at) first_job,
               MAX(first_seen_at) latest_job,
               COALESCE(SUM(CASE WHEN telegram_sent=1 THEN 1 ELSE 0 END),0) jobs_delivered,
               COALESCE(SUM(CASE WHEN sent_to_n8n=1 THEN 1 ELSE 0 END),0) jobs_dispatched
        FROM jobs
        """
    ).fetchone()
    decisions = connection.execute(
        "SELECT COUNT(*) decisions, MIN(decided_at) first_decision, MAX(decided_at) latest_decision FROM targeting_decisions"
    ).fetchone()
    claims = connection.execute(
        """
        SELECT COUNT(*) claims,
               COALESCE(SUM(CASE WHEN delivery_state='sent' THEN 1 ELSE 0 END),0) claims_sent,
               MIN(reserved_at) first_claim, MAX(COALESCE(sent_at,reserved_at)) latest_claim
        FROM telegram_delivery_claims
        """
    ).fetchone()
    # Aggregate aliases are not exposed by PRAGMA; use stable positional names.
    source_keys = ("runs", "first_run", "latest_run", "requests", "scanned", "normalized", "duplicates", "rejected", "eligible", "new_eligible", "source_run_telegram", "downstream", "errors", "providers_run")
    job_keys = ("jobs_stored", "first_job", "latest_job", "jobs_delivered", "jobs_dispatched")
    decision_keys = ("decisions", "first_decision", "latest_decision")
    claim_keys = ("claims", "claims_sent", "first_claim", "latest_claim")
    source_map = dict(zip(source_keys, source or ()))
    job_map = dict(zip(job_keys, jobs or ()))
    decision_map = dict(zip(decision_keys, decisions or ()))
    claim_map = dict(zip(claim_keys, claims or ()))
    return {**source_map, **job_map, **decision_map, **claim_map}


def daily_history(connection: sqlite3.Connection, *, tz=None) -> pd.DataFrame:
    active_tz = tz or system_timezone()
    runs = pd.read_sql_query(
        """
        SELECT started_at, request_count, raw_count, normalized_count, duplicate_count,
               eligible_count, new_eligible_count,
               reject_role_count+reject_location_count+reject_hard_requirement_count+
               reject_company_count+reject_other_targeting_count AS rejected,
               error_count
        FROM source_runs ORDER BY started_at
        """,
        connection,
    )
    jobs = pd.read_sql_query("SELECT first_seen_at FROM jobs ORDER BY first_seen_at", connection)
    claims = pd.read_sql_query(
        "SELECT COALESCE(sent_at,reserved_at) AS occurred_at FROM telegram_delivery_claims WHERE delivery_state='sent'",
        connection,
    )
    outbox_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_operational_outbox'"
    ).fetchone()
    operational = (
        pd.read_sql_query(
            """
            SELECT created_at,delivery_state,notification_kind
            FROM telegram_operational_outbox
            WHERE notification_kind='adapter_run_summary'
            ORDER BY created_at
            """,
            connection,
        )
        if outbox_exists
        else pd.DataFrame()
    )

    if runs.empty and jobs.empty and claims.empty:
        return pd.DataFrame()
    parts: list[pd.DataFrame] = []
    if not runs.empty:
        runs["Date"] = runs["started_at"].map(lambda value: local_date(value, tz=active_tz))
        grouped = runs.groupby("Date", dropna=True).agg(
            **{
                "Source runs": ("started_at", "count"),
                "Provider requests": ("request_count", "sum"),
                "Opportunities scanned": ("raw_count", "sum"),
                "Normalized": ("normalized_count", "sum"),
                "Duplicates": ("duplicate_count", "sum"),
                "Rejected": ("rejected", "sum"),
                "Eligible": ("eligible_count", "sum"),
                "New eligible": ("new_eligible_count", "sum"),
                "Errors": ("error_count", "sum"),
            }
        )
        parts.append(grouped)
    if not jobs.empty:
        jobs["Date"] = jobs["first_seen_at"].map(lambda value: local_date(value, tz=active_tz))
        parts.append(jobs.groupby("Date", dropna=True).size().rename("Jobs stored").to_frame())
    if not claims.empty:
        claims["Date"] = claims["occurred_at"].map(lambda value: local_date(value, tz=active_tz))
        parts.append(claims.groupby("Date", dropna=True).size().rename("Telegram deliveries").to_frame())
    if not operational.empty:
        operational["Date"] = operational["created_at"].map(
            lambda value: local_date(value, tz=active_tz)
        )
        parts.append(
            operational.groupby("Date", dropna=True)
            .size()
            .rename("Adapter summary cards generated")
            .to_frame()
        )
        delivered = operational.loc[operational["delivery_state"] == "sent"]
        if not delivered.empty:
            parts.append(
                delivered.groupby("Date", dropna=True)
                .size()
                .rename("Adapter summary cards delivered")
                .to_frame()
            )
    combined = pd.concat(parts, axis=1).fillna(0).sort_index()
    return combined.astype(int)


def operational_summary_metrics(connection: sqlite3.Connection) -> dict[str, Any]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_operational_outbox'"
    ).fetchone()
    if not exists:
        return {"available": False}
    row = connection.execute(
        """
        SELECT COUNT(*) generated,
               COALESCE(SUM(CASE WHEN delivery_state='sent' THEN 1 ELSE 0 END),0) delivered,
               COALESCE(SUM(CASE WHEN delivery_state='retry' THEN 1 ELSE 0 END),0) retrying,
               COALESCE(SUM(CASE WHEN delivery_state='pending' THEN 1 ELSE 0 END),0) pending,
               COALESCE(SUM(CASE WHEN delivery_state='uncertain' THEN 1 ELSE 0 END),0) uncertain,
               MIN(created_at) earliest,
               MAX(created_at) latest
        FROM telegram_operational_outbox
        WHERE notification_kind='adapter_run_summary'
        """
    ).fetchone()
    keys = ("generated", "delivered", "retrying", "pending", "uncertain", "earliest", "latest")
    return {"available": True, **dict(zip(keys, row or (0, 0, 0, 0, 0, None, None)))}


def rejection_intelligence(connection: sqlite3.Connection) -> pd.DataFrame:
    data = pd.read_sql_query(
        """
        SELECT primary_category AS machine_category, COUNT(*) AS Count
        FROM targeting_decisions
        WHERE primary_category LIKE 'REJECT_%'
        GROUP BY primary_category ORDER BY Count DESC
        """,
        connection,
    )
    if data.empty:
        return data
    total = int(data["Count"].sum())
    data.insert(0, "Reason", data["machine_category"].map(humanize_machine_value))
    data["Share"] = data["Count"].map(lambda value: round(100.0 * int(value) / total, 1) if total else 0.0)
    return data[["Reason", "Count", "Share"]]


def provider_intelligence(connection: sqlite3.Connection, *, maturity_runs: int = 3) -> pd.DataFrame:
    data = pd.read_sql_query(
        """
        SELECT source_name AS Provider, COUNT(*) AS Runs,
               SUM(request_count) AS Requests, SUM(raw_count) AS Scanned,
               SUM(normalized_count) AS Normalized, SUM(eligible_count) AS Eligible,
               SUM(new_eligible_count) AS "New eligible", SUM(error_count) AS Errors,
               SUM(CASE WHEN run_status='completed' THEN 1 ELSE 0 END) AS Completed,
               MAX(completed_at) AS "Last completed"
        FROM source_runs GROUP BY source_name ORDER BY Scanned DESC, Runs DESC
        """,
        connection,
    )
    if data.empty:
        return data
    data["Eligible yield %"] = data.apply(
        lambda row: round(100.0 * int(row["Eligible"]) / int(row["Normalized"]), 2)
        if int(row["Normalized"] or 0) else None,
        axis=1,
    )
    data["Mature"] = (data["Runs"] >= maturity_runs) & (data["Normalized"] >= 100)
    return data


def n8n_execution_metrics(
    workflow_id: str,
    *,
    database_path: Path = DEFAULT_N8N_DB,
    tz=None,
) -> tuple[dict[str, Any], pd.DataFrame]:
    empty = {
        "available": False,
        "workflow_id": workflow_id,
        "workflow_name": None,
        "active": None,
        "executions": 0,
        "successful": 0,
        "failed": 0,
        "running": 0,
        "canceled": 0,
        "first_execution": None,
        "latest_execution": None,
        "last_success": None,
    }
    if not workflow_id or not database_path.is_file():
        return empty, pd.DataFrame()
    uri = f"file:{database_path}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        workflow = connection.execute(
            "SELECT name,active FROM workflow_entity WHERE id=?", (workflow_id,)
        ).fetchone()
        rows = connection.execute(
            """
            SELECT status,mode,COALESCE(startedAt,createdAt) started,
                   COALESCE(stoppedAt,startedAt,createdAt) finished
            FROM execution_entity
            WHERE workflowId=? AND deletedAt IS NULL
            ORDER BY COALESCE(startedAt,createdAt)
            """,
            (workflow_id,),
        ).fetchall()
    except sqlite3.Error:
        return empty, pd.DataFrame()
    finally:
        if "connection" in locals():
            connection.close()
    if workflow is None:
        return empty, pd.DataFrame()
    statuses = [str(row[0] or "unknown").casefold() for row in rows]
    successes = [row for row, status in zip(rows, statuses) if status == "success"]
    summary = {
        **empty,
        "available": True,
        "workflow_name": workflow[0],
        "active": bool(workflow[1]),
        "executions": len(rows),
        "successful": statuses.count("success"),
        "failed": sum(status in {"error", "failed", "crashed"} for status in statuses),
        "running": sum(status in {"running", "new", "waiting"} for status in statuses),
        "canceled": sum(status in {"canceled", "cancelled"} for status in statuses),
        "first_execution": rows[0][2] if rows else None,
        "latest_execution": rows[-1][3] if rows else None,
        "last_success": successes[-1][3] if successes else None,
    }
    daily_rows = []
    for row, status in zip(rows, statuses):
        day = local_date(row[2], tz=tz or system_timezone())
        if day is not None:
            daily_rows.append({"Date": day, "Executions": 1, "Successful": int(status == "success"), "Failed": int(status in {"error", "failed", "crashed"})})
    daily = pd.DataFrame(daily_rows)
    if not daily.empty:
        daily = daily.groupby("Date", dropna=True).sum().sort_index()
    return summary, daily
