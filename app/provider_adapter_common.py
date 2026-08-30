from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.database import get_connection, get_setting, initialize_database
from app.dashboard_targeting_gate import filter_dashboard_jobs, record_source_metrics
from app.job_store import save_job


FetchBoard = Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load_boards(provider: str, limit: int) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM provider_board_registry
            WHERE lower(provider) = lower(?) AND enabled = 1
            ORDER BY priority_weight DESC, company_name COLLATE NOCASE
            LIMIT ?
            """,
            (provider, max(1, min(int(limit), 100))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _source_enabled(provider: str) -> bool:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT enabled FROM source_health WHERE lower(source_name) = lower(?)",
            (provider,),
        ).fetchone()
        return bool(row and int(row["enabled"] or 0) == 1)
    finally:
        connection.close()


def _source_tier(provider: str) -> int:
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT source_tier FROM source_health WHERE lower(source_name)=lower(?)",
            (provider,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"Provider source is not registered: {provider}")
    return int(row["source_tier"])


def _update_board(board_id: int, *, success: bool, job_count: int, error: str | None) -> None:
    connection = get_connection()
    try:
        connection.execute(
            """
            UPDATE provider_board_registry
            SET last_verified_at = ?, health_status = ?, last_job_count = ?,
                notes = CASE WHEN ? IS NULL THEN notes ELSE ? END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                _utc_now(),
                "healthy" if success else "failed",
                int(job_count),
                error,
                str(error or "")[:1500] if error else None,
                int(board_id),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _update_health(provider: str, *, successes: int, failures: int, raw: int, error: str | None) -> None:
    status = "healthy" if successes and not failures else "degraded" if successes else "failed"
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE source_health
            SET last_run_at = ?,
                last_success_at = CASE WHEN ? > 0 THEN ? ELSE last_success_at END,
                last_failure_at = CASE WHEN ? > 0 THEN ? ELSE last_failure_at END,
                consecutive_failures = CASE WHEN ? > 0 THEN 0 ELSE consecutive_failures + 1 END,
                last_http_status = CASE WHEN ? > 0 THEN 200 ELSE last_http_status END,
                jobs_found_last_run = ?, health_status = ?, last_error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE source_name = ?
            """,
            (
                _utc_now(), successes, _utc_now(), failures, _utc_now(), successes,
                successes, raw, status, error, provider,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(f"Provider health rejected for unregistered source: {provider}")
        us_count = connection.execute(
            """
            SELECT COUNT(*) FROM provider_board_registry
            WHERE lower(provider)=lower(?) AND enabled=1
              AND lower(us_relevance) IN ('us', 'high', 'confirmed', 'nationwide')
            """,
            (provider,),
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE adapter_coverage
            SET implemented=1, live_tested=?, enabled=(
                    SELECT enabled FROM source_health WHERE source_name=?
                ), health_status=?, us_board_count=?, blocked_reason=NULL,
                last_verified_at=?, updated_at=CURRENT_TIMESTAMP
            WHERE provider=?
            """,
            (int(successes > 0), provider, status, int(us_count), _utc_now(), provider),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def run_provider(
    provider: str,
    fetch_board: FetchBoard,
    *,
    max_boards: int | None = None,
) -> dict[str, Any]:
    run_started = time.perf_counter()
    run_started_at = datetime.now(timezone.utc).isoformat()
    initialize_database()
    orchestration = get_setting("orchestration", {}) or {}
    if bool(orchestration.get("maintenance_mode", False)):
        return {
            "success": True,
            "worker_action": "maintenance_skip",
            "source": provider,
            "network_request_made": False,
        }
    if not _source_enabled(provider):
        return {
            "success": True,
            "worker_action": "disabled_skip",
            "source": provider,
            "network_request_made": False,
        }

    runtime = get_setting("provider_runtime", {}) or {}
    try:
        retry_attempts = max(1, int(runtime["retry_attempts"]))
        retry_backoff = max(0.0, float(runtime["retry_backoff_seconds"]))
        resolved_max_boards = max(1, int(
            runtime["provider_board_run_limit"] if max_boards is None else max_boards
        ))
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("Canonical provider adapter execution policy is incomplete.") from None
    source_tier = _source_tier(provider)
    boards = _load_boards(provider, resolved_max_boards)
    all_jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    requests_count = 0
    duration_ms = 0.0
    successes = 0

    for board in boards:
        error: Exception | None = None
        result: dict[str, Any] | None = None
        for attempt in range(retry_attempts):
            try:
                result = fetch_board(board, runtime)
                error = None
                break
            except Exception as caught:
                error = caught
                if attempt + 1 < retry_attempts and retry_backoff:
                    time.sleep(retry_backoff * (attempt + 1))
        if error is not None or result is None:
            message = f"{type(error).__name__}: {error}" if error else "Unknown provider failure"
            errors.append({"company": board["company_name"], "error": message[:1500]})
            _update_board(int(board["id"]), success=False, job_count=0, error=message)
            continue
        jobs = [dict(job) for job in (result.get("jobs") or []) if isinstance(job, Mapping)]
        for job in jobs:
            job["source_tier"] = source_tier
            job.setdefault("_query_name", "Configured provider boards")
        all_jobs.extend(jobs)
        requests_count += int(result.get("requests") or 0)
        duration_ms += float(result.get("duration_ms") or 0.0)
        successes += 1
        _update_board(int(board["id"]), success=True, job_count=len(jobs), error=None)

    filtered = filter_dashboard_jobs(all_jobs)
    eligible = list(filtered["eligible_jobs"])
    stored: list[dict[str, Any]] = []
    connection = get_connection()
    try:
        for job in eligible:
            stored.append(save_job(connection, job, actor=f"{provider.casefold()}_worker"))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    inserted = sum(1 for item in stored if item.get("inserted"))
    database_duplicates = sum(
        1 for item in stored if item.get("primary_category") == "DUPLICATE"
    )
    filtered["requests"] = requests_count
    filtered["run_started_at"] = run_started_at
    filtered["duration_ms"] = round((time.perf_counter() - run_started) * 1000, 2)
    filtered.setdefault("_stage_durations_ms", {})["FETCH"] = round(duration_ms, 2)
    filtered["errors"] = errors
    filtered["query_requests"] = [{
        "query_name": "Configured provider boards",
        "role_family": "",
        "requests": requests_count,
        "raw": len(all_jobs),
        "errors": len(errors),
        "duration_ms": round(duration_ms, 2),
    }]
    record_source_metrics(
        provider,
        raw_jobs=len(all_jobs),
        eligible_jobs=len(eligible),
        inserted_jobs=inserted,
        duplicate_jobs=database_duplicates,
        provider_used=provider,
        filter_summary=filtered,
    )
    last_error = json.dumps(errors, ensure_ascii=False)[:2000] if errors else None
    _update_health(
        provider,
        successes=successes,
        failures=len(errors),
        raw=len(all_jobs),
        error=last_error,
    )
    return {
        "success": bool(successes) and not errors,
        "partial_success": bool(successes and errors),
        "worker_action": "run",
        "source": provider,
        "network_request_made": bool(boards),
        "configured_boards": len(boards),
        "successful_boards": successes,
        "failed_boards": len(errors),
        "requests": requests_count,
        "raw": len(all_jobs),
        "normalized": filtered["raw_normalized"],
        "eligible": filtered["eligible"],
        "inserted": inserted,
        "database_duplicates": database_duplicates,
        "accounting_delta": filtered["accounting_delta"],
        "rules_hash": filtered["targeting_rules_hash"],
        "errors": errors,
    }
