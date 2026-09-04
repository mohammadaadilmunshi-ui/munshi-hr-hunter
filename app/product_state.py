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
    """Return only values actually represented in the jobs table."""
    return {
        "locations": [str(row["value"]) for row in _rows("SELECT DISTINCT location_raw AS value FROM jobs WHERE trim(COALESCE(location_raw,'')) != '' ORDER BY value LIMIT 100")],
        "sources": [str(row["value"]) for row in _rows("SELECT DISTINCT source AS value FROM jobs WHERE trim(COALESCE(source,'')) != '' ORDER BY value LIMIT 100")],
        "employment": [str(row["value"]) for row in _rows("SELECT DISTINCT employment_type AS value FROM jobs WHERE trim(COALESCE(employment_type,'')) != '' ORDER BY value LIMIT 40")],
        "remote": [str(row["value"]) for row in _rows("SELECT DISTINCT remote_type AS value FROM jobs WHERE trim(COALESCE(remote_type,'')) != '' ORDER BY value LIMIT 20")],
    }


SEARCH_SCOPES = {"title_description", "title_company"}
RESULT_SETS = {"all", "saved", "passed"}


def fetch_jobs(
    *, query: str = "", exclude: str = "", location: str = "", source: str = "",
    workplace: str = "", employment_type: str = "", minimum_score: float = 0,
    saved_only: bool = False, include_skipped: bool = False, page: int = 1,
    search_scope: str = "title_description",
    result_set: str = "all",
    page_size: int = 16,
) -> tuple[list[dict[str, Any]], int]:
    """Parameterized, stable job-browser query. Unknown/null data stays null."""
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
            scope = "title_description"
        if scope == "title_company":
            conditions.append("(j.title LIKE ? COLLATE NOCASE OR j.company_name LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle))
        else:
            conditions.append("(j.title LIKE ? COLLATE NOCASE OR j.description_raw LIKE ? COLLATE NOCASE)")
            params.extend((needle, needle))
    if exclude.strip():
        conditions.append("(j.title NOT LIKE ? COLLATE NOCASE AND j.company_name NOT LIKE ? COLLATE NOCASE AND j.description_raw NOT LIKE ? COLLATE NOCASE)")
        needle = f"%{exclude.strip()}%"
        params.extend((needle, needle, needle))
    for column, value in (("j.location_raw", location), ("j.source", source), ("j.remote_type", workplace), ("j.employment_type", employment_type)):
        if value:
            conditions.append(f"{column} = ?")
            params.append(value)
    if minimum_score > 0:
        conditions.append("COALESCE(j.hunter_score, -1) >= ?")
        params.append(float(minimum_score))
    where = " AND ".join(conditions)
    count = _rows(f"SELECT COUNT(*) AS count FROM jobs j LEFT JOIN product_job_state s ON s.job_id=j.id WHERE {where}", params)[0]["count"]
    limit = max(1, min(int(page_size), 48))
    offset = max(0, int(page) - 1) * limit
    rows = _rows(
        f"""SELECT j.*, COALESCE(s.saved,0) AS saved, COALESCE(s.skipped,0) AS skipped,
                   r.n8n_status, r.final_ats_score, r.resume_pdf_url, r.cover_letter_doc_url,
                   q.queue_status
            FROM jobs j
            LEFT JOIN product_job_state s ON s.job_id=j.id
            LEFT JOIN n8n_results r ON r.id=(SELECT r2.id FROM n8n_results r2 WHERE r2.job_id=j.id ORDER BY r2.id DESC LIMIT 1)
            LEFT JOIN n8n_dispatch_queue q ON q.id=(SELECT q2.id FROM n8n_dispatch_queue q2 WHERE q2.job_id=j.id ORDER BY q2.id DESC LIMIT 1)
            WHERE {where}
            ORDER BY COALESCE(j.hunter_score,-1) DESC, j.first_seen_at DESC, j.id DESC
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


def tracker_status(raw_status: Any, queue_status: Any = None) -> str:
    """Presentation-only status mapper: a completed package is never Submitted."""
    status = str(raw_status or "").strip().casefold()
    queue = str(queue_status or "").strip().casefold()
    if status in {"submitted", "submission_confirmed", "externally_submitted"}:
        return "Submitted"
    if status in {"application_ready", "final_ready", "completed", "complete"}:
        return "Prepared"
    if status in {"ats_review_required", "review_required", "needs_review"}:
        return "Needs you"
    if status in {"failed", "error", "rejected"} or queue == "failed":
        return "Failed"
    if queue in {"pending", "queued", "accepted", "dispatching", "dispatched", "running", "waiting", "processing"}:
        return "In progress"
    if queue == "skipped":
        return "Skipped"
    return "Other"


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
        (max(1, min(int(limit), 250)),),
    )
    for row in rows:
        row["display_status"] = tracker_status(row.get("n8n_status"), row.get("queue_status"))
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
        "needs_you": sum(record["display_status"] == "Needs you" for record in records),
        "in_progress": sum(record["display_status"] == "In progress" for record in records),
    }


def research_snapshot() -> dict[str, Any]:
    """Bounded, explainable read model for the customer-facing research view."""
    connection = get_connection()
    try:
        ensure_schema(connection)
        headline = dict(connection.execute(
            """SELECT COUNT(*) AS jobs, ROUND(AVG(hunter_score), 1) AS average_score,
                      SUM(CASE WHEN hard_rejection_reason IS NOT NULL
                                AND trim(hard_rejection_reason) != '' THEN 1 ELSE 0 END) AS blocked
                 FROM jobs"""
        ).fetchone())
        source_quality = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(source), ''), 'Source not recorded') AS source,
                      COUNT(*) AS opportunities, ROUND(AVG(hunter_score), 1) AS average_match,
                      SUM(CASE WHEN hard_rejection_reason IS NULL
                                OR trim(hard_rejection_reason) = '' THEN 1 ELSE 0 END) AS eligible_records
                 FROM jobs GROUP BY COALESCE(NULLIF(trim(source), ''), 'Source not recorded')
                 ORDER BY opportunities DESC, source ASC LIMIT 20"""
        ).fetchall()]
        blockers = [dict(row) for row in connection.execute(
            """SELECT hard_rejection_reason AS reason, COUNT(*) AS count
                 FROM jobs WHERE hard_rejection_reason IS NOT NULL
                   AND trim(hard_rejection_reason) != ''
                 GROUP BY hard_rejection_reason ORDER BY count DESC, reason ASC LIMIT 12"""
        ).fetchall()]
        authorization = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(work_authorization), ''), 'Not recorded') AS status,
                      COUNT(*) AS count
                 FROM jobs GROUP BY COALESCE(NULLIF(trim(work_authorization), ''), 'Not recorded')
                 ORDER BY count DESC, status ASC LIMIT 12"""
        ).fetchall()]
        trends = [dict(row) for row in connection.execute(
            """SELECT substr(first_seen_at, 1, 10) AS date, COUNT(*) AS opportunities,
                      ROUND(AVG(hunter_score), 1) AS average_match
                 FROM jobs WHERE trim(COALESCE(first_seen_at, '')) != ''
                 GROUP BY substr(first_seen_at, 1, 10)
                 ORDER BY date DESC LIMIT 14"""
        ).fetchall()]
        ats = dict(connection.execute(
            """SELECT COUNT(final_ats_score) AS scored_packages,
                      ROUND(AVG(final_ats_score), 1) AS average_ats_score
                 FROM n8n_results WHERE final_ats_score IS NOT NULL"""
        ).fetchone())
        health = [dict(row) for row in connection.execute(
            """SELECT source_name, health_status, last_success_at, jobs_found_last_run
                 FROM source_health ORDER BY source_name LIMIT 30"""
        ).fetchall()]
        top_matches = [dict(row) for row in connection.execute(
            """SELECT id, company_name, title, hunter_score, source, target_track,
                      location_raw, remote_type, employment_type, work_authorization,
                      hard_rejection_reason
                 FROM jobs WHERE hunter_score IS NOT NULL
                ORDER BY hunter_score DESC, first_seen_at DESC, id DESC LIMIT 8"""
        ).fetchall()]
        query_performance = [dict(row) for row in connection.execute(
            """SELECT COALESCE(NULLIF(trim(query_name), ''), 'Query not recorded') AS query_name,
                      COUNT(*) AS runs, COALESCE(SUM(raw_count), 0) AS raw_records,
                      COALESCE(SUM(eligible_count), 0) AS eligible_records, MAX(started_at) AS last_run
                 FROM source_runs
                GROUP BY COALESCE(NULLIF(trim(query_name), ''), 'Query not recorded')
                ORDER BY last_run DESC LIMIT 12"""
        ).fetchall()]
    finally:
        connection.close()
    return {
        "headline": headline, "source_quality": source_quality,
        "blockers": blockers, "authorization": authorization,
        "trends": list(reversed(trends)), "ats": ats, "health": health,
        "top_matches": top_matches, "query_performance": query_performance,
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
        connection.execute("INSERT INTO auto_prepare_lanes(name,enabled,filter_json,min_score,volume_mode,daily_limit) VALUES (?,?,?,?,?,?)", (name.strip()[:120], 0, json.dumps(filters, sort_keys=True), float(min_score), mode, max(1, int(daily_limit or 1)) if mode == "custom_limit" else None))
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


def candidate_facts() -> list[dict[str, Any]]:
    return _rows("SELECT * FROM candidate_profile_facts ORDER BY fact_key")


def save_candidate_fact(key: str, value: str) -> None:
    if not key.strip():
        raise ValueError("A fact label is required.")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.execute("INSERT INTO candidate_profile_facts(fact_key,value_json,updated_at) VALUES (?,?,CURRENT_TIMESTAMP) ON CONFLICT(fact_key) DO UPDATE SET value_json=excluded.value_json, updated_at=CURRENT_TIMESTAMP", (key.strip()[:120], json.dumps(value.strip())))
        connection.commit()
    finally:
        connection.close()
