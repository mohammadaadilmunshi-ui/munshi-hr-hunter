from __future__ import annotations

from app import database
from app import randomized_source_runner
from app.randomized_source_runner import _is_blocked, _last_json, _record_runner_failure


def test_last_json_returns_outer_worker_result_not_nested_object() -> None:
    text = 'log line\n{"success": false, "errors": [], "nested": {"count": 3}}\n'
    assert _last_json(text) == {
        "success": False,
        "errors": [],
        "nested": {"count": 3},
    }


def test_last_json_prefers_latest_complete_top_level_result() -> None:
    text = '{"old": true}\nnoise\n{"success": true, "detail": {"ok": true}}\n'
    assert _last_json(text) == {"success": True, "detail": {"ok": True}}


def test_due_sort_key_orders_mixed_sqlite_and_iso_timestamps_chronologically() -> None:
    sources = [{"source_name": "Ashby"}, {"source_name": "Remotive"}]
    timers = {
        "Ashby": {"next_allowed_at": "2026-08-24T02:54:17+00:00"},
        "Remotive": {"next_allowed_at": "2026-08-24 06:08:36"},
    }

    ordered = sorted(
        sources,
        key=lambda source: randomized_source_runner._due_sort_key(source, timers),
    )

    assert [source["source_name"] for source in ordered] == ["Ashby", "Remotive"]


def test_clean_structured_success_ignores_incidental_http_status_output() -> None:
    payload = {
        "success": True,
        "partial_success": False,
        "raw_jobs_found": 50,
        "errors": [],
    }
    assert _is_blocked("optional probe returned HTTP 403 before fallback", payload) is False


def test_structured_rate_limit_evidence_remains_blocked() -> None:
    assert _is_blocked("", {"success": False, "errors": [{"status": 429}]}) is True
    assert _is_blocked("", {"success": True, "blocked": True, "errors": []}) is True


def test_runner_failure_updates_canonical_source_health(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode,health_status
            ) VALUES ('Fixture Source',1,1,60,'free','healthy')
            """
        )
        connection.commit()
    finally:
        connection.close()

    run_id = _record_runner_failure(
        "Fixture Source", status="timeout", returncode=124, error="fixture timeout"
    )
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT health_status,consecutive_failures,last_error,raw_jobs_last_run,"
            "normalized_jobs_last_run,eligible_jobs_last_run,error_count_last_run,"
            "last_run_id,provider_used_last_run FROM source_health "
            "WHERE source_name='Fixture Source'"
        ).fetchone()
        source_run = connection.execute(
            "SELECT run_status,error_count,accounting_delta FROM source_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        stage_count = connection.execute(
            "SELECT COUNT(*) FROM source_run_stages WHERE run_id=?", (run_id,)
        ).fetchone()[0]
    finally:
        connection.close()
    assert dict(row) == {
        "health_status": "failed",
        "consecutive_failures": 1,
        "last_error": "fixture timeout",
        "raw_jobs_last_run": 0,
        "normalized_jobs_last_run": 0,
        "eligible_jobs_last_run": 0,
        "error_count_last_run": 1,
        "last_run_id": run_id,
        "provider_used_last_run": "scheduler",
    }
    assert dict(source_run) == {
        "run_status": "failed",
        "error_count": 1,
        "accounting_delta": 0,
    }
    assert stage_count == 7
