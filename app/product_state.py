"""Truth-bound read models and explicit user-state mutations for Product UI.

This module deliberately contains no scheduling or dispatch-on-render behaviour.
The product UI reads canonical records here and delegates any preparation action to
the existing guarded worker.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable

from app.database import get_connection, get_setting, save_setting
from app.candidate_artifacts import (
    clear_master_resume as clear_indexed_master_resume,
    designate_master_resume,
    master_resume as indexed_master_resume,
)
from app.tenant_foundation import DEFAULT_TENANT_ID, DEFAULT_USER_ID, associate_owned_record, current_owner
from app.tenant_foundation import ensure_schema as ensure_tenant_foundation_schema


PRODUCT_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_job_state (
    job_id INTEGER PRIMARY KEY,
    saved INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_product_job_state_saved ON product_job_state(saved, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_product_job_state_skipped ON product_job_state(skipped, updated_at DESC);

CREATE TABLE IF NOT EXISTS auto_prepare_lanes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    filter_json TEXT NOT NULL DEFAULT '{}',
    min_score REAL,
    volume_mode TEXT NOT NULL DEFAULT 'unlimited',
    daily_limit INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS candidate_profile_facts (
    fact_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    source_label TEXT NOT NULL DEFAULT 'Candidate',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gmail_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT NOT NULL UNIQUE,
    thread_id TEXT,
    category TEXT NOT NULL DEFAULT 'unclassified',
    subject TEXT,
    sender TEXT,
    received_at TEXT,
    snippet TEXT,
    body_text TEXT,
    classification_evidence TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gmail_messages_category_time ON gmail_messages(category, received_at DESC);
"""

VALID_VIEWS = {"dashboard", "jobs", "auto-prepare", "tracker", "profile", "research", "settings"}
VALID_VOLUME_MODES = {"unlimited", "custom_limit", "paused", "pause_after_batch"}


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        connection.executescript(PRODUCT_SCHEMA)
        ensure_tenant_foundation_schema(connection)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def valid_view(value: Any) -> str:
    candidate = str(value or "dashboard").strip().casefold()
    return candidate if candidate in VALID_VIEWS else "dashboard"


def _rows(sql: str, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        return [dict(row) for row in connection.execute(sql, tuple(parameters)).fetchall()]
    finally:
        connection.close()


def job_filters() -> dict[str, list[str]]:
    """Return only filter values actually represented in canonical jobs."""
    specs = {
        "locations": ("location_raw", 500),
        "sources": ("source", 150),
        "employment": ("employment_type", 80),
        "remote": ("remote_type", 40),
        "target_tracks": ("target_track", 100),
    }
    result: dict[str, list[str]] = {}
    for key, (column, limit) in specs.items():
        rows = _rows(
            f"SELECT DISTINCT {column} AS value FROM jobs "
            f"WHERE trim(COALESCE({column},'')) != '' "
            f"ORDER BY value LIMIT {int(limit)}"
        )
        result[key] = [str(row["value"]) for row in rows]
    return result

SEARCH_SCOPES = {"all_fields", "title_description", "title_company"}
RESULT_SETS = {"all", "saved", "passed"}

def fetch_jobs(
    *,
    query: str = "",
    exclude: str = "",
    location: str = "",
    source: str = "",
    workplace: str = "",
    employment_type: str = "",
    minimum_score: float = 0,
    maximum_score: float = 100,
    target_track: str = "",
    eligibility: str = "all",
    freshness_days: int = 0,
    ats_only: bool = False,
    saved_only: bool = False,
    include_skipped: bool = False,
    page: int = 1,
    search_scope: str = "all_fields",
    result_set: str = "all",
    sort_by: str = "match_desc",
    page_size: int = 16,
) -> tuple[list[dict[str, Any]], int]:
    """Parameterized advanced job browser using stored canonical evidence."""
    conditions = ["COALESCE(s.hidden, 0) = 0"]
    params: list[Any] = []

    normalized_result_set = str(result_set or "all").strip().casefold()
    if normalized_result_set not in RESULT_SETS:
        normalized_result_set = "all"
    if normalized_result_set == "passed":
        conditions.append("COALESCE(s.skipped, 0) = 1")
    elif not include_skipped:
        conditions.append("COALESCE(s.skipped, 0) = 0")
    if saved_only or normalized_result_set == "saved":
        conditions.append("COALESCE(s.saved, 0) = 1")

    if query.strip():
        needle = f"%{query.strip()}%"
        scope = str(search_scope or "").strip().casefold()
        if scope not in SEARCH_SCOPES:
            scope = "all_fields"
        if scope == "title_company":
            conditions.append("(j.title LIKE ? COLLATE NOCASE OR j.company_name LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle))
        elif scope == "title_description":
            conditions.append("(j.title LIKE ? COLLATE NOCASE OR j.description_raw LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle))
        else:
            conditions.append("(j.title LIKE ? COLLATE NOCASE OR j.company_name LIKE ? COLLATE NOCASE OR j.description_raw LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle, needle))

    excluded_terms = tuple(dict.fromkeys(
        term.strip()
        for term in re.split(r"[,;\n]+", str(exclude or ""))
        if term.strip()
    ))
    for term in excluded_terms:
        needle = f"%{term}%"
        conditions.append("(COALESCE(j.title,'') NOT LIKE ? COLLATE NOCASE AND COALESCE(j.company_name,'') NOT LIKE ? COLLATE NOCASE AND COALESCE(j.description_raw,'') NOT LIKE ? COLLATE NOCASE)")
        params.extend((needle, needle, needle))

    for column, value in (
        ("j.location_raw", location),
        ("j.source", source),
        ("j.remote_type", workplace),
        ("j.employment_type", employment_type),
        ("j.target_track", target_track),
    ):
        if value:
            conditions.append(f"{column} = ?")
            params.append(value)

    if float(minimum_score or 0) > 0:
        conditions.append("COALESCE(j.hunter_score, -1) >= ?")
        params.append(float(minimum_score))
    if float(maximum_score if maximum_score is not None else 100) < 100:
        conditions.append("COALESCE(j.hunter_score, 101) <= ?")
        params.append(float(maximum_score))

    normalized_eligibility = str(eligibility or "all").strip().casefold()
    if normalized_eligibility == "unblocked":
        conditions.append("(j.hard_rejection_reason IS NULL OR trim(j.hard_rejection_reason) = '')")
    elif normalized_eligibility == "blocked":
        conditions.append("(j.hard_rejection_reason IS NOT NULL AND trim(j.hard_rejection_reason) != '')")

    days = max(0, min(int(freshness_days or 0), 3650))
    if days:
        conditions.append("datetime(j.first_seen_at) >= datetime('now', ?)")
        params.append(f"-{days} days")
    if ats_only:
        conditions.append("r.final_ats_score IS NOT NULL")

    where = " AND ".join(conditions)
    joins = """
        LEFT JOIN product_job_state s ON s.job_id=j.id
        LEFT JOIN n8n_results r
          ON r.id=(SELECT r2.id FROM n8n_results r2 WHERE r2.job_id=j.id ORDER BY r2.id DESC LIMIT 1)
    """
    count = _rows(
        f"SELECT COUNT(*) AS count FROM jobs j {joins} WHERE {where}",
        params,
    )[0]["count"]

    sort_map = {
        "match_desc": "COALESCE(j.hunter_score,-1) DESC, j.first_seen_at DESC, j.id DESC",
        "newest": "j.first_seen_at DESC, j.id DESC",
        "oldest": "j.first_seen_at ASC, j.id ASC",
        "company": "LOWER(COALESCE(j.company_name,'')) ASC, COALESCE(j.hunter_score,-1) DESC, j.id DESC",
        "ats_desc": "COALESCE(r.final_ats_score,-1) DESC, COALESCE(j.hunter_score,-1) DESC, j.id DESC",
    }
    order_by = sort_map.get(str(sort_by or "").strip().casefold(), sort_map["match_desc"])
    limit = max(1, min(int(page_size), 48))
    offset = max(0, int(page) - 1) * limit

    rows = _rows(
        f"""SELECT j.*, COALESCE(s.saved,0) AS saved, COALESCE(s.skipped,0) AS skipped,
                   r.n8n_status, r.final_ats_score, r.resume_pdf_url, r.cover_letter_doc_url,
                   q.queue_status
              FROM jobs j
              {joins}
              LEFT JOIN n8n_dispatch_queue q
                ON q.id=(SELECT q2.id FROM n8n_dispatch_queue q2 WHERE q2.job_id=j.id ORDER BY q2.id DESC LIMIT 1)
             WHERE {where}
             ORDER BY {order_by}
             LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    )
    return rows, int(count)

def set_job_state(job_id: int, *, saved: bool | None = None, skipped: bool | None = None) -> None:
    """Persist only an explicit user action; never called by a page render."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        existing = connection.execute("SELECT saved, skipped FROM product_job_state WHERE job_id=?", (int(job_id),)).fetchone()
        values = dict(existing) if existing else {"saved": 0, "skipped": 0}
        if saved is not None:
            values["saved"] = int(saved)
        if skipped is not None:
            values["skipped"] = int(skipped)
        connection.execute(
            """INSERT INTO product_job_state(job_id,saved,skipped,updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(job_id) DO UPDATE SET saved=excluded.saved, skipped=excluded.skipped, updated_at=CURRENT_TIMESTAMP""",
            (int(job_id), values["saved"], values["skipped"]),
        )
        connection.commit()
    finally:
        connection.close()


def _pretty_status(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(":", " ").replace("_", " ").replace("-", " ")
    words = [word for word in text.split() if word]
    acronyms = {"ats": "ATS", "n8n": "n8n", "hr": "HR", "api": "API"}
    return " ".join(acronyms.get(word.casefold(), word.capitalize()) for word in words)


def tracker_status(raw_status: Any, queue_status: Any = None) -> str:
    """Map workflow evidence into genuine user-facing lifecycle states."""
    status = str(raw_status or "").strip().casefold()
    queue = str(queue_status or "").strip().casefold()
    if status in {"submitted", "submission_confirmed", "externally_submitted"}:
        return "Submitted"
    if status in {"application_ready", "final_ready", "final_ready_deterministic_95_plus", "completed", "complete", "package_prepared", "prepared"}:
        return "Prepared"
    if status in {"ats_review_required", "review_required", "needs_review", "truth_review_required", "manual_review_required", "placement_or_polish_review_required"}:
        return "Needs review"
    if status in {"rejected_by_dashboard_targeting", "targeting_rejected", "blocked", "work_authorization_blocked", "eligibility_blocked"}:
        return "Blocked"
    if status in {"failed", "error"} or queue == "failed":
        return "Failed"
    if queue in {"pending", "queued", "accepted", "dispatching", "dispatched", "running", "waiting", "processing"}:
        return "In progress"
    if queue == "skipped":
        return "Skipped"
    if status:
        return f"Workflow: {_pretty_status(status)}"
    if queue in {"completed", "complete"}:
        return "Queue completed"
    if queue:
        return f"Queue: {_pretty_status(queue)}"
    return "Status not recorded"

def tracker_rows(limit: int = 100) -> list[dict[str, Any]]:
    rows = _rows(
        """SELECT j.id AS job_id,j.company_name,j.title,j.hunter_score,j.apply_url,
                   r.n8n_status,r.final_ats_score,r.resume_doc_url,r.resume_pdf_url,r.cover_letter_doc_url,
                   r.sent_at,r.completed_at,q.queue_status,q.queued_at,q.updated_at
              FROM jobs j
              LEFT JOIN n8n_results r ON r.id=(SELECT id FROM n8n_results WHERE job_id=j.id ORDER BY id DESC LIMIT 1)
              LEFT JOIN n8n_dispatch_queue q ON q.id=(SELECT id FROM n8n_dispatch_queue WHERE job_id=j.id ORDER BY id DESC LIMIT 1)
             WHERE r.id IS NOT NULL OR q.id IS NOT NULL
             ORDER BY COALESCE(r.completed_at,q.updated_at,q.queued_at,j.updated_at) DESC LIMIT ?""",
        (max(1, min(int(limit), 1000)),),
    )
    for row in rows:
        raw_result = str(row.get("n8n_status") or "").strip()
        raw_queue = str(row.get("queue_status") or "").strip()
        row["display_status"] = tracker_status(raw_result, raw_queue)
        evidence = []
        if raw_result:
            evidence.append(f"workflow={raw_result}")
        if raw_queue:
            evidence.append(f"queue={raw_queue}")
        row["status_evidence"] = " · ".join(evidence) or "No raw lifecycle state recorded"
    return rows

def activity_summary(*, limit: int = 250) -> dict[str, int]:
    """Return bounded, evidence-backed activity counts for product surfaces.

    These are intentionally derived from the same recent queue/result records as
    the tracker.  They are not a quota, prediction, or assertion that an ATS
    submission happened without a recorded submission state.
    """
    records = tracker_rows(limit=limit)
    today = date.today().isoformat()

    def happened_today(record: dict[str, Any]) -> bool:
        value = record.get("completed_at") or record.get("sent_at") or record.get("updated_at") or record.get("queued_at")
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            # SQLite CURRENT_TIMESTAMP is UTC but has no offset. Treat it as
            # such before comparing against the dashboard host's local day.
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            return timestamp.astimezone().date().isoformat() == today
        except (TypeError, ValueError):
            return False

    return {
        "recent_evidence": len(records),
        "prepared_today": sum(record["display_status"] == "Prepared" and happened_today(record) for record in records),
        "submitted_today": sum(record["display_status"] == "Submitted" and happened_today(record) for record in records),
        "needs_you": sum(record["display_status"] == "Needs review" for record in records),
        "in_progress": sum(record["display_status"] == "In progress" for record in records),
    }


def research_snapshot() -> dict[str, Any]:
    """Evidence-backed product research including lifetime discovery telemetry."""
    from app.presentation_analytics import lifetime_metrics

    connection = get_connection()
    try:
        ensure_schema(connection)
        headline = dict(connection.execute(
            """SELECT COUNT(*) AS jobs, ROUND(AVG(hunter_score), 1) AS average_score,
                      SUM(CASE WHEN hard_rejection_reason IS NOT NULL AND trim(hard_rejection_reason) != '' THEN 1 ELSE 0 END) AS blocked
                 FROM jobs"""
        ).fetchone())
        source_quality = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(source), ''), 'Source not recorded') AS source,
                      COUNT(*) AS opportunities, ROUND(AVG(hunter_score), 1) AS average_match,
                      SUM(CASE WHEN hard_rejection_reason IS NULL OR trim(hard_rejection_reason) = '' THEN 1 ELSE 0 END) AS eligible_records
                 FROM jobs GROUP BY COALESCE(NULLIF(trim(source), ''), 'Source not recorded')
                 ORDER BY opportunities DESC, source ASC LIMIT 30"""
        ).fetchall()]
        blockers = [dict(row) for row in connection.execute(
            """SELECT hard_rejection_reason AS reason, COUNT(*) AS count
                 FROM jobs WHERE hard_rejection_reason IS NOT NULL AND trim(hard_rejection_reason) != ''
                 GROUP BY hard_rejection_reason ORDER BY count DESC, reason ASC LIMIT 20"""
        ).fetchall()]
        authorization = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(work_authorization), ''), 'Not recorded') AS status, COUNT(*) AS count
                 FROM jobs GROUP BY COALESCE(NULLIF(trim(work_authorization), ''), 'Not recorded')
                 ORDER BY count DESC, status ASC LIMIT 20"""
        ).fetchall()]
        trends = [dict(row) for row in connection.execute(
            """SELECT substr(first_seen_at, 1, 10) AS date, COUNT(*) AS opportunities,
                      ROUND(AVG(hunter_score), 1) AS average_match
                 FROM jobs WHERE trim(COALESCE(first_seen_at, '')) != ''
                 GROUP BY substr(first_seen_at, 1, 10) ORDER BY date DESC LIMIT 30"""
        ).fetchall()]
        ats = dict(connection.execute(
            """SELECT COUNT(final_ats_score) AS scored_packages, ROUND(AVG(final_ats_score), 1) AS average_ats_score
                 FROM n8n_results WHERE final_ats_score IS NOT NULL"""
        ).fetchone())
        health = [dict(row) for row in connection.execute(
            """SELECT source_name, health_status, last_success_at, jobs_found_last_run
                 FROM source_health ORDER BY source_name LIMIT 50"""
        ).fetchall()]
        top_matches = [dict(row) for row in connection.execute(
            """SELECT id, company_name, title, hunter_score, source, target_track,
                      location_raw, remote_type, employment_type, work_authorization,
                      hard_rejection_reason
                 FROM jobs WHERE hunter_score IS NOT NULL
                ORDER BY hunter_score DESC, first_seen_at DESC, id DESC LIMIT 20"""
        ).fetchall()]
        query_performance = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(query_name), ''), 'Query not recorded') AS query_name,
                      COUNT(*) AS runs, COALESCE(SUM(raw_count), 0) AS raw_records,
                      COALESCE(SUM(eligible_count), 0) AS eligible_records, MAX(started_at) AS last_run
                 FROM source_runs GROUP BY COALESCE(NULLIF(trim(query_name), ''), 'Query not recorded')
                ORDER BY last_run DESC LIMIT 20"""
        ).fetchall()]
        source_telemetry = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(source_name), ''), 'Source not recorded') AS source,
                      COUNT(*) AS runs, COALESCE(SUM(raw_count),0) AS scanned,
                      COALESCE(SUM(normalized_count),0) AS normalized,
                      COALESCE(SUM(eligible_count),0) AS eligible,
                      COALESCE(SUM(new_eligible_count),0) AS new_eligible,
                      MAX(COALESCE(completed_at,started_at)) AS last_run
                 FROM source_runs GROUP BY COALESCE(NULLIF(trim(source_name), ''), 'Source not recorded')
                ORDER BY scanned DESC, source ASC LIMIT 30"""
        ).fetchall()]
        try:
            lifetime = lifetime_metrics(connection)
        except sqlite3.Error:
            lifetime = {"runs": 0, "scanned": 0, "normalized": 0, "eligible": 0,
                        "jobs_stored": int(headline.get("jobs") or 0), "decisions": 0,
                        "jobs_delivered": 0, "jobs_dispatched": 0}
    finally:
        connection.close()
    return {
        "headline": headline, "source_quality": source_quality,
        "blockers": blockers, "authorization": authorization,
        "trends": list(reversed(trends)), "ats": ats, "health": health,
        "top_matches": top_matches, "query_performance": query_performance,
        "source_telemetry": source_telemetry, "lifetime": lifetime,
    }

def volume_policy() -> dict[str, Any]:
    policy = dict(get_setting("product_automation_policy_v1", {}) or {})
    review_preference = dict(get_setting("product_review_preference_v1", {}) or {})
    mode = str(policy.get("mode") or "unlimited")
    return {
        "mode": mode if mode in VALID_VOLUME_MODES else "unlimited",
        "daily_limit": policy.get("daily_limit"),
        "review_first": bool(review_preference.get("review_first", policy.get("review_first", True))),
    }


def save_review_preference(review_first: bool) -> None:
    """Persist a UI preference without activating or changing auto-dispatch policy."""
    save_setting(
        "product_review_preference_v1", {"review_first": bool(review_first)},
        changed_by="streamlit:product-settings",
    )


def save_volume_policy(mode: str, daily_limit: int | None, review_first: bool) -> None:
    normalized = str(mode).strip().casefold()
    if normalized not in VALID_VOLUME_MODES:
        raise ValueError("Unknown automation volume mode")
    save_setting("product_automation_policy_v1", {"mode": normalized, "daily_limit": max(1, int(daily_limit or 1)) if normalized == "custom_limit" else None, "review_first": bool(review_first)}, changed_by="streamlit:product-settings")


def lanes() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM auto_prepare_lanes ORDER BY updated_at DESC, id DESC")


def create_lane(name: str, filters: dict[str, Any], min_score: float, mode: str, daily_limit: int | None) -> None:
    if not name.strip():
        raise ValueError("A lane name is required.")
    if mode not in VALID_VOLUME_MODES:
        raise ValueError("Unknown lane volume mode.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        cursor = connection.execute("INSERT INTO auto_prepare_lanes(name,enabled,filter_json,min_score,volume_mode,daily_limit) VALUES (?,?,?,?,?,?)", (name.strip()[:120], 0, json.dumps(filters, sort_keys=True), float(min_score), mode, max(1, int(daily_limit or 1)) if mode == "custom_limit" else None))
        associate_owned_record(connection, record_domain="auto_prepare_lane", record_key=cursor.lastrowid)
        connection.commit()
    finally:
        connection.close()


def update_lane(
    lane_id: int, *, name: str, filters: dict[str, Any], min_score: float | None,
) -> None:
    """Edit a saved target filter; lane volume is intentionally not an authority."""
    if not name.strip():
        raise ValueError("A lane name is required.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            """UPDATE auto_prepare_lanes
               SET name=?, filter_json=?, min_score=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (name.strip()[:120], json.dumps(filters, sort_keys=True), min_score, int(lane_id)),
        )
        connection.commit()
    finally:
        connection.close()


def set_lane_enabled(lane_id: int, enabled: bool) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute(
            "UPDATE auto_prepare_lanes SET enabled=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(bool(enabled)), int(lane_id)),
        )
        connection.commit()
    finally:
        connection.close()


def delete_lane(lane_id: int) -> None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("DELETE FROM auto_prepare_lanes WHERE id=?", (int(lane_id),))
        connection.commit()
    finally:
        connection.close()


def _lane_keywords(lane: dict[str, Any]) -> tuple[str, ...]:
    try:
        filters = json.loads(str(lane.get("filter_json") or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    raw = filters.get("keywords", "") if isinstance(filters, dict) else ""
    values = raw if isinstance(raw, (list, tuple)) else re.split(r"[,\n]+", str(raw or ""))
    return tuple(dict.fromkeys(str(value).strip().casefold() for value in values if str(value).strip()))


def enabled_lanes(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return saved enabled filters in deterministic order for the producer."""
    ensure_schema(connection)
    return [dict(row) for row in connection.execute(
        "SELECT * FROM auto_prepare_lanes WHERE enabled=1 ORDER BY id ASC"
    ).fetchall()]


def lane_matches_job(lane: dict[str, Any], job: dict[str, Any]) -> bool:
    """Match lane role keywords only against role identity fields.

    Lanes are narrowing role filters. Job-description prose must not broaden a
    lane accidentally just because a generic keyword appears in responsibilities.
    """
    keywords = _lane_keywords(lane)
    if not keywords:
        return False
    text = "\n".join(str(job.get(key) or "") for key in (
        "title", "target_track",
    )).casefold()
    return any(keyword in text for keyword in keywords)


def master_resume() -> dict[str, Any]:
    """Return the current tenant's explicit, n8n-evidenced designation only."""
    record = indexed_master_resume()
    if record:
        return record
    # The former singleton setting was written only by an explicit designation
    # action.  Preserve it during the transition *only* if its exact URL can be
    # resolved to stored n8n evidence; otherwise it is deliberately ignored.
    legacy = dict(get_setting("candidate_master_resume_v1", {}) or {})
    try:
        return designate_master_resume(
            job_id=int(legacy["job_id"]), reference=str(legacy["url"]),
            label=str(legacy.get("label") or "Master resume"),
            source_label=str(legacy.get("source") or "Legacy explicit designation"),
        )
    except (KeyError, TypeError, ValueError):
        return {}


def save_master_resume(job_id: int, url: str, label: str, *, source: str = "Existing generated resume artifact") -> None:
    """Persist an explicit designation for a URL actually stored by n8n."""
    designate_master_resume(job_id=int(job_id), reference=url, label=label, source_label=source)


def clear_master_resume() -> None:
    clear_indexed_master_resume()
    # Prevent a cleared legacy singleton setting from being re-adopted on the
    # next compatibility read. It belongs only to the default singleton, so a
    # future tenant cannot clear another tenant's retired compatibility value.
    owner = current_owner()
    if owner.tenant_id == DEFAULT_TENANT_ID and owner.user_id == DEFAULT_USER_ID:
        save_setting("candidate_master_resume_v1", {}, changed_by="streamlit:product-profile")

def candidate_facts() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM candidate_profile_facts ORDER BY fact_key")


def save_candidate_fact(key: str, value: str) -> None:
    if not key.strip():
        raise ValueError("A fact label is required.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        fact_key = key.strip()[:120]
        connection.execute("INSERT INTO candidate_profile_facts(fact_key,value_json,updated_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(fact_key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP", (fact_key, json.dumps(value.strip())))
        associate_owned_record(connection, record_domain="candidate_profile_fact", record_key=fact_key)
        connection.commit()
    finally:
        connection.close()
