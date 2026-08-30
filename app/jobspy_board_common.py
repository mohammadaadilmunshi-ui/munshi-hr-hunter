from __future__ import annotations
# AADIL_JOBSPY_SITE_PRESERVING_V1_1

import contextlib
import fcntl
import inspect
import json
import os
import re
import secrets
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit, urlunsplit

from app.database import DB_PATH, ROOT_DIR, get_connection
from app.runtime_config import (
    n8n_database_path,
    n8n_workflow_id,
    telegram_batch_limit,
)
from app.job_duplicate_guard import canonical_url, store_with_global_dedupe
from app.google_jobs_provider import provider_status, serpapi_google_jobs
from app.jobspy_pipeline import collect_jobspy_jobs
from app.telegram_auto_dispatch import dispatch_unsent_jobs

ROOT = ROOT_DIR
DB = DB_PATH
CONFIG_PATH = ROOT / "config" / "jobspy_boards.json"
LOCK_DIR = ROOT / "data" / "jobspy_board_locks"
STATE_TABLE = "google_indeed_jobspy_state"

STATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {STATE_TABLE} (
    source_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    external_enabled INTEGER NOT NULL DEFAULT 0,
    next_run_at TEXT,
    blocked_until TEXT,
    consecutive_blocks INTEGER NOT NULL DEFAULT 0,
    last_started_at TEXT,
    last_finished_at TEXT,
    last_status TEXT,
    last_result_json TEXT NOT NULL DEFAULT '{{}}',
    role_cursor INTEGER NOT NULL DEFAULT 0,
    location_cursor INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


def now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ") if value else None


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if result.tzinfo is not None:
            result = result.astimezone().replace(tzinfo=None)
        return result.replace(microsecond=0)
    except ValueError:
        return None


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def source_config(source_key: str) -> dict[str, Any]:
    for item in load_config()["sources"].values():
        if item["source_key"] == source_key:
            return item
    raise RuntimeError(f"Unknown source key: {source_key}")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(STATE_SQL)
    columns = {
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({STATE_TABLE})")
    }
    for name, ddl in (
        ("role_cursor", "INTEGER NOT NULL DEFAULT 0"),
        ("location_cursor", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in columns:
            connection.execute(
                f'ALTER TABLE {STATE_TABLE} ADD COLUMN "{name}" {ddl}'
            )


def effective_cadence(cfg: dict[str, Any], dashboard_cadence: Any) -> int:
    try:
        configured = int(dashboard_cadence)
    except (TypeError, ValueError):
        configured = int(cfg["default_cadence_minutes"])
    return max(int(cfg["minimum_cadence_minutes"]), configured)


def next_phase_time(
    cadence_minutes: int,
    phase_minute: int,
    after: datetime | None = None,
) -> datetime:
    # Compatibility name retained. V5 intentionally removes fixed wall-clock
    # phases and returns base cooldown plus a fresh random delay.
    point = (after or now_local()).replace(microsecond=0)
    cadence = max(60, int(cadence_minutes))
    low = max(7, round(cadence * 0.10))
    high = max(low, min(120, round(cadence * 0.35)))
    jitter = secrets.SystemRandom().randint(low, high)
    return point + timedelta(minutes=cadence + jitter)


def active_work_reason() -> str:
    try:
        process_text = subprocess.run(
            ["ps", "-axo", "command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout.lower()
        for marker in (
            "app.manual_input_worker",
            "app.stored_job_n8n_worker",
            "manual_input_worker.py",
            "stored_job_n8n_worker.py",
        ):
            if marker in process_text:
                return f"active process detected: {marker}"
    except Exception:
        pass

    if DB.exists():
        connection = sqlite3.connect(DB, timeout=10)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in ("n8n_dispatch_queue", "dispatch_queue", "n8n_queue"):
                if table not in tables:
                    continue
                columns = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                status_col = (
                    "queue_status" if "queue_status" in columns
                    else "status" if "status" in columns
                    else None
                )
                if not status_col:
                    continue
                count = connection.execute(
                    f"""
                    SELECT COUNT(*) FROM {table}
                    WHERE lower(COALESCE({status_col}, '')) IN (
                      'pending','queued','accepted','dispatching',
                      'dispatched','running','waiting','processing'
                    )
                    """
                ).fetchone()[0]
                if count:
                    return f"{count} open item(s) in {table}"

            if "telegram_manual_runs" in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(telegram_manual_runs)"
                    )
                }
                if "status" in columns:
                    count = connection.execute(
                        """
                        SELECT COUNT(*) FROM telegram_manual_runs
                        WHERE lower(COALESCE(status, '')) IN (
                          'collecting','processing','dispatching','running',
                          'waiting_callback','accepted'
                        )
                        """
                    ).fetchone()[0]
                    if count:
                        return f"{count} active Telegram manual run(s)"
        finally:
            connection.close()

    n8n_database = n8n_database_path()
    if n8n_database.exists():
        connection = sqlite3.connect(
            f"file:{n8n_database}?mode=ro", uri=True, timeout=10
        )
        connection.execute("PRAGMA query_only=ON")
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "execution_entity" in tables:
                columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(execution_entity)"
                    )
                }
                status_col = "status" if "status" in columns else None
                workflow_col = (
                    "workflowId" if "workflowId" in columns
                    else "workflow_id" if "workflow_id" in columns
                    else None
                )
                if status_col:
                    query = (
                        "SELECT COUNT(*) FROM execution_entity "
                        f"WHERE lower(COALESCE({status_col}, '')) "
                        "IN ('new','running','waiting')"
                    )
                    parameters: tuple[Any, ...] = ()
                    if workflow_col:
                        query += f" AND {workflow_col} = ?"
                        parameters = (n8n_workflow_id(),)
                    count = connection.execute(query, parameters).fetchone()[0]
                    if count:
                        return f"{count} active production n8n execution(s)"
        finally:
            connection.close()
    return ""


@contextlib.contextmanager
def source_lock(source_key: str) -> Iterator[bool]:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    handle = (LOCK_DIR / f"{source_key}.lock").open("a+")
    acquired = False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _flatten_errors(summary: Any) -> str:
    try:
        return json.dumps(summary, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(summary).lower()


_BLOCK_TEXT_MARKERS = (
    "/sorry/",
    "captcha",
    "verify you are human",
    "unusual traffic",
    "too many requests",
    "access denied",
    "temporarily blocked",
    "rate limit",
    "rate-limit",
)
_HTTP_429_PATTERN = re.compile(r"(?<!\d)429(?!\d)")


def _contains_provider_block_signal(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 429
    text = str(value).casefold()
    if _HTTP_429_PATTERN.search(text):
        return True
    return any(marker in text for marker in _BLOCK_TEXT_MARKERS)


def _provider_block_candidates(summary: Any) -> list[Any]:
    if not isinstance(summary, dict):
        return []

    values: list[Any] = []

    for key in ("blocked", "rate_limited"):
        if summary.get(key) is True:
            values.append(True)

    for key in ("http_status", "status_code"):
        if summary.get(key) is not None:
            values.append(summary.get(key))

    for key in (
        "errors",
        "error",
        "provider_error",
        "status_text",
        "provider_status_text",
        "message",
    ):
        value = summary.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        elif value not in (None, "", [], {}):
            values.append(value)

    for collection_key in ("provider_attempts", "attempts"):
        attempts = summary.get(collection_key) or []
        if not isinstance(attempts, list):
            continue
        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            for key in (
                "http_status",
                "status_code",
                "error",
                "provider_error",
                "status_text",
                "message",
            ):
                value = attempt.get(key)
                if value not in (None, "", [], {}):
                    values.append(value)
            if attempt.get("blocked") is True or attempt.get("rate_limited") is True:
                values.append(True)

    return values


def detect_block(summary: dict[str, Any], error: Exception | None = None) -> bool:
    if error is not None and _contains_provider_block_signal(error):
        return True
    return any(
        _contains_provider_block_signal(value)
        for value in _provider_block_candidates(summary)
    )


def _source_health_row(
    connection: sqlite3.Connection,
    display_name: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM source_health WHERE source_name = ?",
        (display_name,),
    ).fetchone()


def source_runtime(source_key: str) -> dict[str, Any]:
    cfg = source_config(source_key)
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        health = _source_health_row(connection, cfg["display_name"])
        state = connection.execute(
            f"SELECT * FROM {STATE_TABLE} WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        enabled = bool(health["enabled"]) if health is not None else False
        cadence = effective_cadence(
            cfg,
            health["cadence_minutes"] if health is not None else None,
        )
        next_run = parse_dt(state["next_run_at"]) if state else None
        blocked_until = parse_dt(state["blocked_until"]) if state else None
        now = now_local()
        due = enabled and not (blocked_until and blocked_until > now) and (
            next_run is None or next_run <= now
        )
        return {
            "source_key": source_key,
            "display_name": cfg["display_name"],
            "dashboard_enabled": enabled,
            "dashboard_cadence_minutes": (
                int(health["cadence_minutes"]) if health is not None else None
            ),
            "effective_cadence_minutes": cadence,
            "next_run_at": iso(next_run),
            "blocked_until": iso(blocked_until),
            "due": due,
            "health_status": health["health_status"] if health else None,
        }
    finally:
        connection.close()


def update_source_health(
    connection: sqlite3.Connection,
    display_name: str,
    *,
    status: str,
    jobs_found: int,
    error: str | None,
    success: bool,
) -> None:
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(source_health)")
    }
    assignments: dict[str, Any] = {
        "health_status": status,
        "jobs_found_last_run": int(jobs_found),
        "last_run_at": iso(now_local()),
        "last_error": error[:2000] if error else None,
        "updated_at": iso(now_local()),
    }
    if success:
        assignments["last_success_at"] = iso(now_local())
    else:
        assignments["last_failure_at"] = iso(now_local())
    assignments = {key: value for key, value in assignments.items() if key in columns}
    if assignments:
        sql = ", ".join(f'"{key}" = ?' for key in assignments)
        connection.execute(
            f"UPDATE source_health SET {sql} WHERE source_name = ?",
            [*assignments.values(), display_name],
        )
    if "consecutive_failures" in columns:
        if success:
            connection.execute(
                "UPDATE source_health SET consecutive_failures = 0 WHERE source_name = ?",
                (display_name,),
            )
        else:
            connection.execute(
                """
                UPDATE source_health
                SET consecutive_failures = consecutive_failures + 1
                WHERE source_name = ?
                """,
                (display_name,),
            )


def _emit_source_result(payload: dict[str, Any]) -> None:
    try:
        from app.source_run_notifier import emit_source_run_result
        emit_source_run_result(payload)
    except Exception as error:
        payload.setdefault("source_notification_errors", []).append(str(error))


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat", "<na>"}:
        return default
    return text or default


def _clean_url(value: Any) -> str:
    return canonical_url(value)


def _value(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def _location_parts(record: dict[str, Any]) -> tuple[str | None, str | None, str]:
    location_value = record.get("location")
    city = _clean_text(record.get("city")) or None
    state = _clean_text(record.get("state")) or None
    country = _clean_text(record.get("country")) or ""
    if isinstance(location_value, dict):
        city = _clean_text(location_value.get("city")) or city
        state = _clean_text(location_value.get("state")) or state
        country = _clean_text(location_value.get("country"), country) or country
        location = ", ".join(part for part in (city, state, country) if part)
    else:
        location = _clean_text(location_value)
    if not location:
        location = ", ".join(part for part in (city, state, country) if part)
    return city, state, location or "Not specified"


def normalize_jobspy_row(record: dict[str, Any], site_name: str) -> dict[str, Any]:
    city, state, location = _location_parts(record)
    company = _clean_text(_value(record, "company", "company_name"), "Unknown Company")
    title = _clean_text(_value(record, "title", "job_title"), "Unknown Position")
    job_url = _clean_url(_value(record, "job_url", "url"))
    direct_url = _clean_url(_value(record, "job_url_direct", "direct_url", "apply_url"))
    minimum = _value(record, "min_amount", "salary_min")
    maximum = _value(record, "max_amount", "salary_max")
    interval = _clean_text(_value(record, "interval", "salary_interval"))
    currency = _clean_text(_value(record, "currency"))
    salary_values = [currency, minimum, "-" if minimum not in (None, "") and maximum not in (None, "") else None, maximum, interval]
    salary_raw = " ".join(_clean_text(value) for value in salary_values if value not in (None, "")) or None
    is_remote = record.get("is_remote")
    remote_type = "Remote" if is_remote is True else "Not specified"
    return {
        "source": f"JobSpy/{site_name}",
        "source_tier": 2,
        "ats_job_id": _clean_text(_value(record, "id", "job_id", "ats_job_id", "job_url")),
        "company_name": company,
        "title": title,
        "location_raw": location,
        "city": city,
        "state": state,
        "country": _clean_text(record.get("country")) or "",
        "remote_type": remote_type,
        "employment_type": _clean_text(_value(record, "job_type", "employment_type"), "Not specified"),
        "job_url": job_url or direct_url,
        "apply_url": direct_url or job_url,
        "description_raw": _clean_text(_value(record, "description", "description_raw"), "Not specified"),
        "salary_raw": salary_raw,
        "date_posted": _clean_text(_value(record, "date_posted", "posted_at")) or None,
    }


def _load_dashboard_targeting() -> tuple[list[str], list[dict[str, Any]]]:
    from app.dashboard_targeting_gate import load_dashboard_targeting_rules
    rules = load_dashboard_targeting_rules()
    return list(rules.matching_roles), [dict(value) for value in rules.location_plan]


def _role_match(title: str, roles: list[str]) -> tuple[bool, str | None, str]:
    from app.relevance import match_target_role
    result = match_target_role(title, roles)
    if isinstance(result, tuple) and len(result) >= 3:
        return bool(result[0]), result[1], str(result[2])
    return bool(result), None, "dashboard_role_match"


def _local_location_match(job: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    location = _clean_text(job.get("location_raw")).lower()
    remote = _clean_text(job.get("remote_type")).lower()
    is_remote = "remote" in location or "remote" in remote or "work from home" in location
    if bool(rule.get("remote_only")):
        return is_remote, "remote_only_match" if is_remote else "remote_required"
    city = _clean_text(rule.get("city")).lower()
    state = _clean_text(rule.get("state")).lower()
    search_location = _clean_text(rule.get("search_location")).lower()
    if city and city in location:
        return True, "city_match"
    state_tokens = {state}
    state_names = {
        "nj": "new jersey", "ny": "new york", "pa": "pennsylvania",
    }
    if state in state_names:
        state_tokens.add(state_names[state])
    if any(token and re.search(rf"\b{re.escape(token)}\b", location) for token in state_tokens):
        return True, "state_match"
    if search_location and search_location in location:
        return True, "search_location_match"
    if is_remote and bool(rule.get("remote_allowed")):
        return True, "remote_allowed"
    return False, "location_not_matched"


def _dashboard_location_match(job: dict[str, Any], rule: dict[str, Any]) -> tuple[bool, str]:
    try:
        from app.discovery_config import matches_location_rule
        attempts: list[Callable[[], Any]] = [
            lambda: matches_location_rule(job, rule),
            lambda: matches_location_rule(job.get("location_raw"), rule),
            lambda: matches_location_rule(
                job.get("location_raw"), job.get("remote_type"), rule
            ),
            lambda: matches_location_rule(
                location_raw=job.get("location_raw"),
                remote_type=job.get("remote_type"),
                rule=rule,
            ),
        ]
        for attempt in attempts:
            try:
                value = attempt()
            except (TypeError, AttributeError, KeyError):
                continue
            if isinstance(value, tuple):
                return bool(value[0]), str(value[1] if len(value) > 1 else "dashboard_location_match")
            if isinstance(value, dict):
                matched = bool(value.get("matched", value.get("match", value)))
                return matched, str(value.get("reason") or "dashboard_location_match")
            return bool(value), "dashboard_location_match"
    except Exception:
        pass
    return _local_location_match(job, rule)


def filter_dashboard_jobs(
    raw_jobs: list[dict[str, Any]],
    *,
    roles: list[str],
    plans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from app.dashboard_targeting_gate import filter_dashboard_jobs as _dashboard_gate
    result = _dashboard_gate(raw_jobs)
    jobs = list(result.pop("eligible_jobs", []))
    return jobs, result


def _records_from_frame(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", False):
        return []
    if hasattr(frame, "to_dict"):
        return [dict(value) for value in frame.to_dict(orient="records")]
    if isinstance(frame, list):
        return [dict(value) for value in frame if isinstance(value, dict)]
    return []


def _scrape_jobs(**kwargs: Any) -> Any:
    from jobspy import scrape_jobs as _aadil_original_scrape_jobs
    from app.free_adapter_policy_v1_1 import make_site_preserving_wrapper as _aadil_make_site_preserving_wrapper
    scrape_jobs = _aadil_make_site_preserving_wrapper(_aadil_original_scrape_jobs)
    signature = inspect.signature(scrape_jobs)
    accepted = {
        key: value for key, value in kwargs.items()
        if key in signature.parameters and value is not None
    }
    return scrape_jobs(**accepted)


def _state_cursors(source_key: str) -> tuple[int, int]:
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        row = connection.execute(
            f"SELECT role_cursor, location_cursor FROM {STATE_TABLE} WHERE source_key = ?",
            (source_key,),
        ).fetchone()
        return (
            int(row["role_cursor"] or 0) if row else 0,
            int(row["location_cursor"] or 0) if row else 0,
        )
    finally:
        connection.close()




def _google_role_family(role: str) -> str:
    text = _clean_text(role).lower()
    if any(term in text for term in ("talent acquisition", "recruiting", "recruiter")):
        return "talent acquisition"
    if any(term in text for term in ("people analytics", "workforce analytics", "hr analytics")):
        return "people analytics"
    if "hris" in text or "human resources information" in text:
        return "HRIS"
    if "compensation" in text or "benefits" in text:
        return "compensation and benefits"
    if "people operations" in text or "people ops" in text:
        return "people operations"
    if "organizational" in text or "talent management" in text:
        return "talent management"
    return "human resources"


def _serpapi_query(role: str, plan: dict[str, Any]) -> tuple[str, str]:
    family = _google_role_family(role)
    location = _clean_text(plan.get("search_location"), "United States")
    if bool(plan.get("remote_only")):
        return f"remote {family} jobs", "United States"
    return f"{family} jobs", location


def _jobspy_google_query(role: str, plan: dict[str, Any]) -> str:
    family = _google_role_family(role)
    location = _clean_text(plan.get("search_location"), "United States")
    if bool(plan.get("remote_only")):
        return f"remote {family} jobs in United States"
    return f"{family} jobs near {location}"


def _jobspy_google_request(
    *,
    query: str,
    results_wanted: int,
) -> list[dict[str, Any]]:
    frame = _scrape_jobs(
        site_name=["google"],
        google_search_term=query,
        results_wanted=results_wanted,
        verbose=0,
    )
    return [
        normalize_jobspy_row(record, "google")
        for record in _records_from_frame(frame)
    ]


def collect_google_jobs(
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # AADIL_GOOGLE_JOBSPY_ONLY_V1
    roles, plans = _load_dashboard_targeting()
    if not roles or not plans:
        raise RuntimeError(
            "Dashboard target roles or location plans are empty."
        )

    role_cursor, location_cursor = _state_cursors(
        cfg["source_key"]
    )
    request_limit = min(
        int(cfg.get("requests_per_run") or 2),
        len(plans),
    )
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    attempts: list[dict[str, Any]] = []
    queries_used: list[str] = []
    query_requests: list[dict[str, Any]] = []

    controlled_query = _clean_text(
        os.getenv("AADIL_GOOGLE_TEST_QUERY")
    )
    controlled_location = _clean_text(
        os.getenv("AADIL_GOOGLE_TEST_LOCATION")
    )
    results_wanted = max(
        10,
        int(cfg.get("results_per_request") or 10),
    )

    for index in range(request_limit):
        plan = plans[
            (location_cursor + index)
            % len(plans)
        ]
        role = roles[
            (role_cursor + index)
            % len(roles)
        ]
        role_text = _clean_text(role)
        location = (
            controlled_location
            if controlled_query and index == 0
            else _clean_text(
                plan.get("search_location"),
                "United States",
            )
        )
        remote_only = bool(
            plan.get("remote_only")
        )

        if controlled_query and index == 0:
            query_variants = [
                controlled_query,
            ]
        elif remote_only:
            query_variants = [
                f"{role_text} remote jobs",
                f"{role_text} remote jobs in the United States",
                f"{role_text} jobs remote",
            ]
        else:
            query_variants = [
                f"{role_text} jobs near {location}",
                f"{role_text} jobs in {location}",
                f"{role_text} {location}",
            ]

        unique_queries: list[str] = []
        seen_queries: set[str] = set()
        for value in query_variants:
            clean_query = _clean_text(value)
            key = clean_query.casefold()
            if not clean_query or key in seen_queries:
                continue
            seen_queries.add(key)
            unique_queries.append(clean_query)

        for query in unique_queries[:3]:
            queries_used.append(query)
            request_started = time.perf_counter()
            try:
                jobs = _jobspy_google_request(
                    query=query,
                    results_wanted=results_wanted,
                )
                duration_ms = round(
                    (time.perf_counter() - request_started) * 1000,
                    2,
                )
                for job in jobs:
                    job["_query_name"] = query
                    job["_role_family"] = _google_role_family(role)
                    job["_matched_rule_id"] = plan.get("rule_id")
                    job["_matched_rule_name"] = plan.get("rule_name")
                raw.extend(jobs)
                attempts.append({
                    "provider": "jobspy_google",
                    "success": True,
                    "raw_jobs_found": len(jobs),
                    "target_role": role,
                    "query": query,
                    "duration_ms": duration_ms,
                    "location": location,
                    "remote_only": remote_only,
                    "paid_api_calls": 0,
                    "serp_calls": 0,
                })
                query_requests.append({
                    "query_name": query,
                    "role_family": _google_role_family(role),
                    "requests": 1,
                    "raw": len(jobs),
                    "errors": 0,
                    "duration_ms": duration_ms,
                    "selection_mode": "configured_cursor_rotation",
                })
                if jobs:
                    break
            except Exception as error:
                duration_ms = round(
                    (time.perf_counter() - request_started) * 1000,
                    2,
                )
                errors.append(
                    f"JobSpy Google {query}: {error}"
                )
                attempts.append({
                    "provider": "jobspy_google",
                    "success": False,
                    "raw_jobs_found": 0,
                    "target_role": role,
                    "query": query,
                    "duration_ms": duration_ms,
                    "location": location,
                    "remote_only": remote_only,
                    "error": str(error),
                    "paid_api_calls": 0,
                    "serp_calls": 0,
                })
                query_requests.append({
                    "query_name": query,
                    "role_family": _google_role_family(role),
                    "requests": 1,
                    "raw": 0,
                    "errors": 1,
                    "duration_ms": duration_ms,
                    "selection_mode": "configured_cursor_rotation",
                })

    filtered, summary = filter_dashboard_jobs(
        raw[: int(cfg["max_raw_jobs"])],
        roles=roles,
        plans=plans,
    )
    providers_with_results = sorted({
        str(item["provider"])
        for item in attempts
        if int(item.get("raw_jobs_found") or 0) > 0
    })
    summary.update({
        "search_strategy": "jobspy_google_only_multi_query_v1",
        "provider_order": ["jobspy_google"],
        "providers_with_results": providers_with_results,
        "provider_attempts": attempts,
        "queries_used": queries_used,
        "query_requests": query_requests,
        "request_count": len(query_requests),
        "errors": errors,
        "partial_success": bool(raw) and bool(errors),
        "paid_provider_connected": False,
        "paid_api_calls": 0,
        "role_cursor_before": role_cursor,
        "role_cursor_after": (
            role_cursor + request_limit
        ) % len(roles),
        "location_cursor_before": location_cursor,
        "location_cursor_after": (
            location_cursor + request_limit
        ) % len(plans),
    })
    return (
        filtered[: int(cfg["max_raw_jobs"])],
        summary,
    )

def collect_linkedin_jobs(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    roles, plans = _load_dashboard_targeting()
    if not roles or not plans:
        raise RuntimeError("Dashboard target roles or location plans are empty.")
    role_cursor, location_cursor = _state_cursors(cfg["source_key"])
    request_count = min(int(cfg["requests_per_run"]), len(plans))
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    plan_results: list[dict[str, Any]] = []
    query_requests: list[dict[str, Any]] = []
    for index in range(request_count):
        plan = plans[(location_cursor + index) % len(plans)]
        role = roles[(role_cursor + index) % len(roles)]
        location = _clean_text(plan.get("search_location"), "United States")
        before = len(raw)
        request_started = time.perf_counter()
        try:
            frame = _scrape_jobs(
                site_name=["linkedin"],
                search_term=role,
                location=location,
                results_wanted=int(cfg["results_per_request"]),
                hours_old=int(cfg["hours_old"]),
                is_remote=True if bool(plan.get("remote_only")) else None,
                linkedin_fetch_description=bool(cfg.get("linkedin_fetch_description", True)),
                verbose=0,
            )
            records = _records_from_frame(frame)
            normalized = [
                normalize_jobspy_row(record, "linkedin")
                for record in records
            ]
            for job in normalized:
                job["_query_name"] = role
                job["_role_family"] = ""
                job["_matched_rule_id"] = plan.get("rule_id")
                job["_matched_rule_name"] = plan.get("rule_name")
            raw.extend(normalized)
            duration_ms = round(
                (time.perf_counter() - request_started) * 1000,
                2,
            )
            plan_results.append({
                "rule_id": plan.get("rule_id"),
                "rule_name": plan.get("rule_name"),
                "search_location": location,
                "target_role": role,
                "raw_jobs_found": len(raw) - before,
                "normalized_jobs": len(normalized),
                "query_name": role,
                "duration_ms": duration_ms,
                "success": True,
            })
            query_requests.append({
                "query_name": role,
                "role_family": "",
                "requests": 1,
                "raw": len(normalized),
                "errors": 0,
                "duration_ms": duration_ms,
                "selection_mode": "configured_cursor_rotation",
            })
        except Exception as error:
            duration_ms = round(
                (time.perf_counter() - request_started) * 1000,
                2,
            )
            errors.append(f"{location}: {error}")
            plan_results.append({
                "rule_id": plan.get("rule_id"),
                "rule_name": plan.get("rule_name"),
                "search_location": location,
                "target_role": role,
                "query_name": role,
                "duration_ms": duration_ms,
                "raw_jobs_found": 0,
                "success": False,
                "error": str(error),
            })
            query_requests.append({
                "query_name": role,
                "role_family": "",
                "requests": 1,
                "raw": 0,
                "errors": 1,
                "duration_ms": duration_ms,
                "selection_mode": "configured_cursor_rotation",
            })
    filtered, summary = filter_dashboard_jobs(raw, roles=roles, plans=plans)
    summary.update({
        "search_strategy": "conservative_linkedin_rotation",
        "linkedin_fetch_description": True,
        "plan_results": plan_results,
        "query_requests": query_requests,
        "request_count": len(query_requests),
        "errors": errors,
        "partial_success": bool(errors) and len(errors) < request_count,
        "role_cursor_before": role_cursor,
        "role_cursor_after": (role_cursor + request_count) % len(roles),
        "location_cursor_before": location_cursor,
        "location_cursor_after": (location_cursor + request_count) % len(plans),
    })
    return filtered[: int(cfg["max_raw_jobs"])], summary


def collect_indeed_jobs(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw_jobs, summary = collect_jobspy_jobs(
        sites=["indeed"],
        results_wanted=int(cfg["results_per_request"]),
        hours_old=int(cfg["hours_old"]),
        source_name=str(cfg.get("display_name") or "Indeed Jobs (JobSpy)"),
    )
    from app.dashboard_targeting_gate import filter_dashboard_jobs as canonical_filter

    provider_raw_count = int(summary.get("raw_jobs_found") or len(raw_jobs))
    filtered = canonical_filter(list(raw_jobs)[: int(cfg["max_raw_jobs"])])
    summary.update({key: value for key, value in filtered.items() if key != "eligible_jobs"})
    # Fetch volume and normalized targeting volume are different stages. A
    # configured normalization cap must not rewrite what the provider returned.
    summary["raw_jobs_found"] = provider_raw_count
    summary["provider_raw_jobs_found"] = provider_raw_count
    return list(filtered.get("eligible_jobs") or []), dict(summary)


def collect_board_jobs(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    site = cfg["site_name"]
    if site == "google":
        return collect_google_jobs(cfg)
    if site == "linkedin":
        return collect_linkedin_jobs(cfg)
    if site == "indeed":
        return collect_indeed_jobs(cfg)
    raise RuntimeError(f"Unsupported independent JobSpy site: {site}")


def _telegram_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    for name in (
        "telegram_messages_sent", "telegram_messages", "messages_sent", "sent"
    ):
        try:
            value = int(payload.get(name) or 0)
            if value:
                return value
        except (TypeError, ValueError):
            continue
    return 0


def _query_storage_attribution(
    jobs: list[dict[str, Any]],
    stored: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], dict[int, str]]:
    new_counts: dict[str, int] = {}
    duplicate_counts: dict[str, int] = {}
    inserted_job_queries: dict[int, str] = {}
    for job, stored_result in zip(jobs, stored, strict=True):
        query_name = str(job.get("_query_name") or "Unattributed")
        if stored_result.get("inserted"):
            new_counts[query_name] = new_counts.get(query_name, 0) + 1
            job_id = int(stored_result.get("job_id") or 0)
            if job_id > 0:
                inserted_job_queries[job_id] = query_name
        else:
            duplicate_counts[query_name] = duplicate_counts.get(query_name, 0) + 1
    return new_counts, duplicate_counts, inserted_job_queries


def _query_telegram_attribution(
    telegram: dict[str, Any],
    inserted_job_queries: dict[int, str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for sent_item in telegram.get("sent") or []:
        if not isinstance(sent_item, dict):
            continue
        query_name = inserted_job_queries.get(int(sent_item.get("job_id") or 0))
        if query_name:
            counts[query_name] = counts.get(query_name, 0) + 1
    return counts


def _dispatch_for_source(source_prefix: str) -> dict[str, Any]:
    # The targeting safety wrapper intentionally exposes *args/**kwargs, so
    # signature inspection cannot discover the parameters supported by the
    # underlying dispatcher. Always scope JobSpy dispatch to this source.
    value = dispatch_unsent_jobs(
        source_prefix=source_prefix,
        limit=telegram_batch_limit(),
    )
    return value if isinstance(value, dict) else {"result": value}


def run_board(
    source_key: str,
    *,
    no_store: bool = False,
    force: bool = False,
    run_now: bool = False,
) -> dict[str, Any]:
    cfg = source_config(source_key)
    runtime = source_runtime(source_key)
    if not force and not runtime["dashboard_enabled"]:
        return {
            "success": True,
            "source": cfg["display_name"],
            "worker_action": "skip",
            "skip_reason": "dashboard_source_disabled",
            "source_state": runtime,
            "network_request_made": False,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
    if not force and run_now:
        blocked_until = parse_dt(runtime.get("blocked_until"))
        if blocked_until and blocked_until > now_local():
            return {
                "success": True,
                "source": cfg["display_name"],
                "worker_action": "skip",
                "skip_reason": "blocked_backoff_active",
                "source_state": runtime,
                "network_request_made": False,
                "telegram_messages": 0,
                "n8n_calls": 0,
            }
    if not force and not run_now and not runtime["due"]:
        return {
            "success": True,
            "source": cfg["display_name"],
            "worker_action": "skip",
            "skip_reason": "cadence_not_due_or_blocked",
            "source_state": runtime,
            "network_request_made": False,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }
    reason = active_work_reason()
    if reason:
        return {
            "success": True,
            "source": cfg["display_name"],
            "worker_action": "skip",
            "skip_reason": reason,
            "network_request_made": False,
            "telegram_messages": 0,
            "n8n_calls": 0,
        }

    with source_lock(source_key) as acquired:
        if not acquired:
            return {
                "success": True,
                "source": cfg["display_name"],
                "worker_action": "skip",
                "skip_reason": "source_locked",
                "network_request_made": False,
                "telegram_messages": 0,
                "n8n_calls": 0,
            }

        started = now_local()
        started_at_utc = datetime.now(timezone.utc).isoformat()
        jobs: list[dict[str, Any]] = []
        summary: dict[str, Any] = {}
        caught: Exception | None = None
        try:
            jobs, summary = collect_board_jobs(cfg)
        except Exception as error:
            caught = error
            summary = {"errors": [str(error)], "raw_jobs_found": 0}

        blocked = detect_block(summary, caught)
        errors = summary.get("errors") or []
        result: dict[str, Any] = {
            "success": caught is None and not blocked,
            "source": cfg["display_name"],
            "site_name": cfg["site_name"],
            "worker_action": "run",
            "run_trigger": (
                "controlled_no_store_test" if no_store
                else "manual_run_now" if run_now
                else "dashboard_scheduler"
            ),
            "configuration_source": "SQLite dashboard",
            "dashboard_enabled": runtime["dashboard_enabled"],
            "dashboard_cadence_minutes": runtime["dashboard_cadence_minutes"],
            "effective_cadence_minutes": runtime["effective_cadence_minutes"],
            "raw_jobs_found": int(summary.get("raw_jobs_found") or len(jobs)),
            "jobs_after_dashboard_filters": len(jobs),
            "max_raw_jobs": int(cfg["max_raw_jobs"]),
            "results_per_request": int(cfg["results_per_request"]),
            "requests_per_run": int(cfg["requests_per_run"]),
            "errors": errors,
            "blocked": blocked,
            "network_request_made": True,
            "no_store": no_store,
            "jobs_inserted": 0,
            "database_duplicates": 0,
            "cross_source_fingerprint_duplicates": 0,
            "inserted_job_ids": [],
            "telegram_messages": 0,
            "n8n_calls": 0,
            "started_at": iso(started),
            "finished_at": iso(now_local()),
            "discovery_summary": summary,
        }
        if no_store:
            return result

        state_connection = sqlite3.connect(DB, timeout=30)
        state_connection.row_factory = sqlite3.Row
        try:
            ensure_schema(state_connection)
            state = state_connection.execute(
                f"SELECT * FROM {STATE_TABLE} WHERE source_key = ?",
                (source_key,),
            ).fetchone()
            consecutive = int(state["consecutive_blocks"] or 0) if state else 0
            role_cursor = int(state["role_cursor"] or 0) if state else 0
            location_cursor = int(state["location_cursor"] or 0) if state else 0
            cadence = runtime["effective_cadence_minutes"]

            if blocked:
                consecutive += 1
                backoffs = list(cfg["backoff_minutes"])
                delay = int(backoffs[min(consecutive - 1, len(backoffs) - 1)])
                blocked_until = now_local() + timedelta(minutes=delay)
                next_run = blocked_until
                status = "blocked_backoff"
                error_text = _flatten_errors(summary)[:2000]
                update_source_health(
                    state_connection,
                    cfg["display_name"],
                    status=status,
                    jobs_found=0,
                    error=error_text,
                    success=False,
                )
            elif caught is not None:
                consecutive = 0
                blocked_until = None
                next_run = next_phase_time(cadence, int(cfg["phase_minute"]))
                status = "error"
                error_text = str(caught)
                update_source_health(
                    state_connection,
                    cfg["display_name"],
                    status=status,
                    jobs_found=0,
                    error=error_text,
                    success=False,
                )
            else:
                stored: list[dict[str, Any]] = []
                connection = get_connection()
                try:
                    for job in jobs:
                        stored.append(
                            store_with_global_dedupe(
                                connection,
                                job,
                                actor=f"{source_key}_worker",
                            )
                        )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
                finally:
                    connection.close()

                inserted = [item for item in stored if item.get("inserted")]
                duplicates = [item for item in stored if not item.get("inserted")]
                (
                    query_new_eligible_counts,
                    query_database_duplicate_counts,
                    inserted_job_queries,
                ) = _query_storage_attribution(jobs, stored)
                summary["query_new_eligible_counts"] = query_new_eligible_counts
                summary["query_database_duplicate_counts"] = (
                    query_database_duplicate_counts
                )
                fingerprint_duplicates = [
                    item for item in duplicates
                    if item.get("global_fingerprint_duplicate")
                ]
                result["jobs_inserted"] = len(inserted)
                result["database_duplicates"] = len(duplicates)
                result["cross_source_fingerprint_duplicates"] = len(
                    fingerprint_duplicates
                )
                result["inserted_job_ids"] = [
                    item.get("job_id") for item in inserted if item.get("job_id")
                ]
                try:
                    telegram = _dispatch_for_source(f"JobSpy/{cfg['site_name']}")
                    result["telegram_messages"] = _telegram_count(telegram)
                    summary["telegram_messages"] = result["telegram_messages"]
                    result["telegram_dispatch_errors"] = telegram.get("errors") or []
                    result["telegram_dispatch_result"] = telegram
                    summary["query_telegram_counts"] = _query_telegram_attribution(
                        telegram,
                        inserted_job_queries,
                    )
                except Exception as error:
                    result["telegram_dispatch_errors"] = [str(error)]

                source_errors = [
                    str(value)
                    for value in (summary.get("errors") or [])
                    if str(value).strip()
                ]
                raw_jobs_found = int(summary.get("raw_jobs_found") or len(jobs))
                blocked_until = None
                next_run = next_phase_time(cadence, int(cfg["phase_minute"]))

                if source_errors and raw_jobs_found <= 0:
                    consecutive += 1
                    status = "error"
                    source_success = False
                    result["success"] = False
                else:
                    consecutive = 0
                    status = "degraded" if source_errors else "healthy"
                    source_success = True

                result["health_status"] = status
                result["partial_success"] = bool(source_errors and raw_jobs_found > 0)
                update_source_health(
                    state_connection,
                    cfg["display_name"],
                    status=status,
                    jobs_found=raw_jobs_found,
                    error=(
                        "; ".join(source_errors)[:2000]
                        if source_errors
                        else None
                    ),
                    success=source_success,
                )
                # AADIL_JOBSPY_STATE_COMMIT_BEFORE_METRICS_V2
                # Release this worker's state/source_health writer transaction
                # before record_source_metrics opens its serialized connection.
                state_connection.commit()
                from app.dashboard_targeting_gate import record_source_metrics
                summary.setdefault("run_started_at", started_at_utc)
                summary["elapsed_ms"] = round(
                    (now_local() - started).total_seconds() * 1000,
                    2,
                )
                providers = summary.get("providers_with_results") or []
                provider_used = ",".join(str(value) for value in providers) or str(cfg.get("site_name") or "jobspy")
                rejected_count = (
                    int(summary.get("excluded_by_role") or 0)
                    + int(summary.get("excluded_by_location") or 0)
                    + int(summary.get("excluded_by_hard_reject") or 0)
                    + int(summary.get("excluded_by_company_blacklist") or 0)
                    + int(summary.get("excluded_by_other_targeting") or 0)
                )
                record_source_metrics(
                    cfg["display_name"],
                    raw_jobs=int(summary.get("raw_jobs_found") or len(jobs)),
                    eligible_jobs=len(jobs),
                    inserted_jobs=len(inserted),
                    duplicate_jobs=len(duplicates),
                    rejected_jobs=rejected_count,
                    provider_used=provider_used,
                    filter_summary=summary,
                )

                role_cursor = int(summary.get("role_cursor_after", role_cursor))
                location_cursor = int(summary.get("location_cursor_after", location_cursor))

            state_connection.execute(
                f"""
                UPDATE {STATE_TABLE}
                SET external_enabled = (
                        SELECT COALESCE(enabled, 0)
                        FROM source_health
                        WHERE source_name = ?
                    ),
                    next_run_at = ?,
                    blocked_until = ?,
                    consecutive_blocks = ?,
                    last_started_at = ?,
                    last_finished_at = ?,
                    last_status = ?,
                    last_result_json = ?,
                    role_cursor = ?,
                    location_cursor = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE source_key = ?
                """,
                (
                    cfg["display_name"],
                    iso(next_run),
                    iso(blocked_until),
                    consecutive,
                    iso(started),
                    iso(now_local()),
                    status,
                    json.dumps(result, ensure_ascii=False, default=str),
                    role_cursor,
                    location_cursor,
                    source_key,
                ),
            )
            state_connection.commit()
            result["status"] = status
            result["next_run_at"] = iso(next_run)
            result["blocked_until"] = iso(blocked_until)
        finally:
            state_connection.close()

        _emit_source_result(result)
        if caught is not None and not blocked:
            raise caught
        return result


def status_rows() -> list[dict[str, Any]]:
    config = load_config()
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        ensure_schema(connection)
        rows: list[dict[str, Any]] = []
        for cfg in config["sources"].values():
            state = connection.execute(
                f"SELECT * FROM {STATE_TABLE} WHERE source_key = ?",
                (cfg["source_key"],),
            ).fetchone()
            health = _source_health_row(connection, cfg["display_name"])
            entry = dict(state) if state else {
                "source_key": cfg["source_key"],
                "display_name": cfg["display_name"],
            }
            entry.update({
                "dashboard_enabled": int(health["enabled"]) if health else 0,
                "dashboard_cadence_minutes": int(health["cadence_minutes"]) if health else None,
                "effective_cadence_minutes": effective_cadence(
                    cfg,
                    health["cadence_minutes"] if health else None,
                ),
                "dashboard_health_status": health["health_status"] if health else None,
                "dashboard_jobs_found_last_run": int(health["jobs_found_last_run"] or 0) if health else 0,
                "site_name": cfg["site_name"],
                "max_raw_jobs": cfg["max_raw_jobs"],
                "minimum_cadence_minutes": cfg["minimum_cadence_minutes"],
                "phase_minute": cfg["phase_minute"],
            })
            rows.append(entry)
        return rows
    finally:
        connection.close()

# AADIL_JOBSPY_COUNTRY_EVIDENCE_PRESERVATION_V2
_aadil_normalize_jobspy_row_before_country_v2 = normalize_jobspy_row


def normalize_jobspy_row(record: dict[str, Any], site_name: str) -> dict[str, Any]:
    normalized = _aadil_normalize_jobspy_row_before_country_v2(record, site_name)

    provider_country = _clean_text(record.get("country")) or None
    location_value = record.get("location")
    city = _clean_text(record.get("city")) or None
    state = _clean_text(record.get("state")) or None

    if isinstance(location_value, dict):
        city = _clean_text(location_value.get("city")) or city
        state = _clean_text(location_value.get("state")) or state
        provider_country = _clean_text(location_value.get("country")) or provider_country
        location = ", ".join(
            value for value in (city, state, provider_country) if value
        )
    else:
        location = _clean_text(location_value)

    if not location:
        location = ", ".join(
            value for value in (city, state, provider_country) if value
        )

    normalized["location_raw"] = location or "Not specified"
    normalized["city"] = city
    normalized["state"] = state
    normalized["country"] = provider_country
    normalized["_provider_country_raw"] = provider_country
    normalized["_country_explicit"] = bool(provider_country)
    return normalized

# AADIL_OPT_US_NATIONWIDE_INTEGRITY_V1
from app.opt_us_nationwide_integrity_v1 import (
    normalize_jobspy_result as _aadil_normalize_jobspy_result_v1,
    persist_jobspy_run_start as _aadil_persist_jobspy_run_start_v1,
)

_aadil_previous_run_board_v1 = run_board
_aadil_previous_emit_source_result_v1 = _emit_source_result
_aadil_current_source_key_v1 = None


def _emit_source_result(payload):
    normalized = _aadil_normalize_jobspy_result_v1(
        payload,
        source_key=_aadil_current_source_key_v1,
    )
    return _aadil_previous_emit_source_result_v1(normalized)


def run_board(*args, **kwargs):
    global _aadil_current_source_key_v1
    source_key = str(args[0]) if args else str(kwargs.get("source_key") or kwargs.get("source") or "")
    previous_source_key = _aadil_current_source_key_v1
    _aadil_current_source_key_v1 = source_key or None
    _aadil_persist_jobspy_run_start_v1(source_key or None)
    try:
        result = _aadil_previous_run_board_v1(*args, **kwargs)
        return _aadil_normalize_jobspy_result_v1(
            result,
            source_key=source_key or None,
        )
    finally:
        _aadil_current_source_key_v1 = previous_source_key
