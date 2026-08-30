from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import database, randomized_source_runner as runner
from app import telegram_run_visibility as visibility
from app.dashboard_targeting_gate import record_source_metrics
from app.source_run_notifier import emit_source_run_result
from app.source_cooldown import ensure_schema as ensure_schedule_schema


def _configure(hunter_db: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    del hunter_db
    monkeypatch.setattr(visibility, "DELIVERY_LOCK", tmp_path / "outbox.lock")
    database.save_setting(
        "runtime",
        {"telegram_enabled": True},
        changed_by="pytest",
    )
    database.save_setting(
        "source_run_notifications",
        {
            "enabled": True,
            "terminal_summary_required": True,
            "contract_enabled_at": "2026-08-25T00:00:00+00:00",
            "empty_result_alerts": True,
            "error_alerts": True,
            "configuration_warning_alerts": True,
            "cadence_skip_alerts": False,
            "disabled_source_alerts": False,
            "apply_to_enabled_sources_only": True,
            "include_filter_reasons": True,
            "cooldown_minutes": 0,
            "missed_due_alert_interval_minutes": 30,
            "notification_style": "one_terminal_card_per_run",
        },
        changed_by="pytest",
    )
    database.save_setting(
        "provider_runtime",
        {
            "source_schedule": {
                "sqlite_write_retry_attempts": 3,
                "sqlite_write_retry_cap_seconds": 0.1,
                "sqlite_write_retry_base_seconds": 0.01,
            }
        },
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        ensure_schedule_schema(connection)
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode,
              health_status,last_http_status,last_error
            ) VALUES('Ashby',1,1,60,'free','healthy',200,NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO source_random_schedule(
              source_name,next_run_at,base_cadence_minutes,jitter_minutes,
              schedule_reason,schedule_state
            ) VALUES('Ashby','2026-08-25 04:00:00',60,0,'normal_completed_run_randomized','cooldown')
            """
        )
        connection.commit()
    finally:
        connection.close()


def _insert_run(
    run_id: str,
    *,
    status: str = "completed",
    raw: int = 10,
    normalized: int = 10,
    duplicates: int = 0,
    rejected: int = 0,
    eligible: int = 2,
    new_eligible: int = 1,
    errors: int = 0,
    detail: dict | None = None,
) -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_runs(
              run_id,source_name,provider,started_at,completed_at,run_status,
              request_count,raw_count,normalized_count,duplicate_count,
              eligible_count,new_eligible_count,reject_role_count,
              duration_ms,error_count,detail_json
            ) VALUES(?,?,?,'2026-08-25 01:00:00','2026-08-25 01:00:21',?,
                     3,?,?,?,?,?,?,21000,?,?)
            """,
            (
                run_id,
                "Ashby",
                "Ashby",
                status,
                raw,
                normalized,
                duplicates,
                eligible,
                new_eligible,
                rejected,
                errors,
                json.dumps(detail or {}),
            ),
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("run_id", "kwargs", "outcome"),
    [
        ("success-results", {}, "completed"),
        ("success-zero", {"raw": 0, "normalized": 0, "eligible": 0, "new_eligible": 0}, "completed_zero"),
        ("all-rejected", {"raw": 12, "normalized": 12, "rejected": 12, "eligible": 0, "new_eligible": 0}, "filtered_only"),
        ("duplicates-only", {"raw": 12, "normalized": 12, "duplicates": 12, "eligible": 0, "new_eligible": 0}, "duplicates_only"),
        ("failed", {"status": "failed", "raw": 0, "normalized": 0, "eligible": 0, "new_eligible": 0, "errors": 1}, "failed"),
        ("timeout", {"status": "failed", "raw": 0, "normalized": 0, "eligible": 0, "new_eligible": 0, "errors": 1, "detail": {"worker_status": "timeout"}}, "timeout"),
        ("degraded", {"status": "degraded", "errors": 2, "detail": {"successful_requests": 8, "failed_requests": 2}}, "degraded"),
    ],
)
def test_every_terminal_outcome_creates_one_summary(
    hunter_db, monkeypatch, tmp_path, run_id, kwargs, outcome
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    _insert_run(run_id, **kwargs)
    first = visibility.enqueue_source_run_summary(run_id)
    second = visibility.enqueue_source_run_summary(run_id)
    assert first["queued"] is True
    assert second["already_exists"] is True
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT logical_id,payload_json FROM telegram_operational_outbox WHERE run_id=?",
            (run_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row["logical_id"] == f"adapter_run_summary:{run_id}"
    assert json.loads(row["payload_json"])["outcome_code"] == outcome


def test_telegram_outage_retries_same_logical_card_then_delivers_once(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    _insert_run("outage-zero", raw=0, normalized=0, eligible=0, new_eligible=0)
    visibility.enqueue_source_run_summary("outage-zero")
    connection = database.get_connection()
    connection.execute("UPDATE telegram_operational_outbox SET next_attempt_at=CURRENT_TIMESTAMP")
    connection.commit()
    connection.close()
    calls: list[str] = []

    def unavailable(_method, _payload):
        calls.append("offline")
        raise ConnectionError("network unavailable")

    first = visibility.deliver_pending_operational_cards(send_function=unavailable)
    assert first["failed"] == 1
    connection = database.get_connection()
    row = connection.execute(
        "SELECT logical_id,delivery_state,attempt_count FROM telegram_operational_outbox"
    ).fetchone()
    assert tuple(row) == ("adapter_run_summary:outage-zero", "retry", 1)
    connection.execute("UPDATE telegram_operational_outbox SET next_attempt_at=CURRENT_TIMESTAMP")
    connection.commit()
    connection.close()

    def recovered(_method, _payload):
        calls.append("online")
        return {"ok": True, "result": {"message_id": 701}}

    second = visibility.deliver_pending_operational_cards(send_function=recovered)
    third = visibility.deliver_pending_operational_cards(send_function=recovered)
    assert second["sent"] == 1
    assert third["attempted"] == 0
    assert calls == ["offline", "online"]


def test_reconciliation_recovers_missing_post_commit_enqueue_without_history_replay(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    _insert_run("committed-not-queued")
    result = visibility.reconcile_terminal_run_outbox()
    assert result == {"reconciled": 1, "examined": 1}
    assert visibility.reconcile_terminal_run_outbox() == {"reconciled": 0, "examined": 0}


def test_no_adapter_due_creates_no_scheduler_heartbeat_card(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    result = visibility.reconcile_missed_due_incidents(
        now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
    )
    assert result["overdue"] == 0
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM telegram_operational_outbox").fetchone()[0] == 0
    finally:
        connection.close()


def test_missed_due_alert_deduplicates_and_success_creates_one_recovery(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    now = datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc)
    first = visibility.reconcile_missed_due_incidents(now=now)
    second = visibility.reconcile_missed_due_incidents(now=now)
    assert len(first["created"]) == 1
    assert second["created"] == []
    connection = database.get_connection()
    connection.execute(
        "UPDATE telegram_operational_outbox SET delivery_state='sent',sent_at=CURRENT_TIMESTAMP WHERE notification_kind='adapter_due_incident'"
    )
    connection.commit()
    connection.close()
    _insert_run("recovery-run")
    result = visibility.enqueue_source_run_summary("recovery-run")
    assert result["recovery_events"] == 1
    visibility.enqueue_source_run_summary("recovery-run")
    connection = database.get_connection()
    try:
        kinds = dict(
            connection.execute(
                "SELECT notification_kind,COUNT(*) FROM telegram_operational_outbox GROUP BY notification_kind"
            ).fetchall()
        )
    finally:
        connection.close()
    assert kinds == {
        "adapter_due_incident": 1,
        "adapter_due_recovery": 1,
        "adapter_run_summary": 1,
    }


def test_missed_due_alerts_obey_global_backlog_escalation_interval(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode,
              health_status,last_http_status,last_error
            ) VALUES('Greenhouse',1,1,60,'free','healthy',200,NULL)
            """
        )
        connection.execute(
            """
            INSERT INTO source_random_schedule(
              source_name,next_run_at,base_cadence_minutes,jitter_minutes,
              schedule_reason,schedule_state
            ) VALUES('Greenhouse','2026-08-25 04:01:00',60,0,
                     'normal_completed_run_randomized','cooldown')
            """
        )
        connection.commit()
    finally:
        connection.close()

    first = visibility.reconcile_missed_due_incidents(
        now=datetime(2026, 8, 25, 5, 0, tzinfo=timezone.utc), max_new=1
    )
    second = visibility.reconcile_missed_due_incidents(
        now=datetime(2026, 8, 25, 5, 2, tzinfo=timezone.utc), max_new=1
    )
    assert len(first["created"]) == 1
    assert second == {
        "overdue": 2,
        "created": [],
        "bounded": True,
        "reason": "global_incident_alert_interval",
    }
    connection = database.get_connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM telegram_adapter_incidents"
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_selected_deferred_adapter_persists_one_due_incident(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "discover_worker", lambda _name: "app.fake_worker")
    monkeypatch.setattr(runner, "_source_timeout_seconds", lambda _name: 60)
    monkeypatch.setattr(
        runner,
        "get_adapter_timer",
        lambda *_args, **_kwargs: {
            "next_allowed_at": "2026-08-25T04:00:00+00:00",
            "due": True,
        },
    )
    monkeypatch.setattr(runner, "mark_source_started", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        runner,
        "mark_source_completed",
        lambda *_args, **_kwargs: {"schedule_state": "deferred"},
    )
    monkeypatch.setattr(
        runner,
        "_run_worker_command",
        lambda *_args, **_kwargs: (
            subprocess.CompletedProcess(
                ["python", "-m", "app.fake_worker"],
                0,
                json.dumps(
                    {
                        "success": True,
                        "worker_action": "skip",
                        "skip_reason": "cadence_not_due_or_blocked",
                    }
                ),
                "",
            ),
            False,
            None,
        ),
    )
    monkeypatch.setattr(
        visibility,
        "deliver_pending_operational_cards",
        lambda **_kwargs: {"attempted": 0, "sent": 0, "failed": 0},
    )
    result = runner.run_one({"source_name": "Ashby"})
    assert result["status"] == "deferred"
    assert result["due_incident"]["queued"] is True
    assert result["due_incident"]["logical_id"] == (
        "adapter_due_incident:Ashby:2026-08-25T04:00:00Z"
    )
    connection = database.get_connection()
    try:
        incident = connection.execute(
            "SELECT reason_code,incident_state FROM telegram_adapter_incidents"
        ).fetchone()
        card = connection.execute(
            "SELECT notification_kind,delivery_state FROM telegram_operational_outbox"
        ).fetchone()
    finally:
        connection.close()
    assert tuple(incident) == ("provider_cadence_deferred", "open")
    assert tuple(card) == ("adapter_due_incident", "pending")


def test_explicit_due_incidents_are_staggered_instead_of_bursting(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode,
              health_status,last_http_status,last_error
            ) VALUES('Greenhouse',1,1,60,'free','healthy',200,NULL)
            """
        )
        connection.commit()
    finally:
        connection.close()
    first = visibility.enqueue_due_incident(
        "Ashby",
        "2026-08-25T04:00:00+00:00",
        reason_code="serialized_queue_wait",
        reason_text="The single-worker lane was serving other active work",
    )
    second = visibility.enqueue_due_incident(
        "Greenhouse",
        "2026-08-25T04:01:00+00:00",
        reason_code="serialized_queue_wait",
        reason_text="The single-worker lane was serving other active work",
    )
    assert first["queued"] is True and first["delivery_delay_seconds"] == 0
    assert second["queued"] is True
    assert second["delivery_delay_seconds"] >= 1798
    connection = database.get_connection()
    try:
        rows = connection.execute(
            """
            SELECT source_name,next_attempt_at FROM telegram_operational_outbox
            ORDER BY datetime(next_attempt_at)
            """
        ).fetchall()
    finally:
        connection.close()
    assert [row["source_name"] for row in rows] == ["Ashby", "Greenhouse"]


def test_card_is_human_readable_local_and_never_leaks_secret_detail(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    _insert_run(
        "secret-failure",
        status="failed",
        raw=0,
        normalized=0,
        eligible=0,
        new_eligible=0,
        errors=1,
        detail={"error": "Authorization: Bearer secret-token api_key=secret"},
    )
    visibility.enqueue_source_run_summary("secret-failure")
    connection = database.get_connection()
    try:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM telegram_operational_outbox WHERE run_id='secret-failure'"
            ).fetchone()[0]
        )
    finally:
        connection.close()
    card = visibility.format_operational_card(visibility.RUN_SUMMARY_KIND, payload)
    assert "MUNSHI APPLY · ADAPTER FAILED" in card
    assert "6:00 PM PDT" in card
    assert "secret-token" not in card
    assert "api_key" not in card
    assert "{" not in card and "}" not in card


def test_missing_run_cannot_create_premature_success_card(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    result = visibility.enqueue_source_run_summary("not-committed")
    assert result == {
        "queued": False,
        "reason": "run_not_committed",
        "run_id": "not-committed",
    }


def test_stale_sending_claim_becomes_uncertain_instead_of_duplicate_replay(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    _insert_run("ambiguous")
    visibility.enqueue_source_run_summary("ambiguous")
    connection = database.get_connection()
    connection.execute(
        """
        UPDATE telegram_operational_outbox
        SET delivery_state='sending',lease_token='old',lease_expires_at='2020-01-01 00:00:00'
        """
    )
    connection.commit()
    connection.close()
    assert visibility.reconcile_stale_delivery_claims() == {"uncertain": 1}
    calls = 0

    def send(_method, _payload):
        nonlocal calls
        calls += 1
        return {"result": {"message_id": 9}}

    assert visibility.deliver_pending_operational_cards(send_function=send)["attempted"] == 0
    assert calls == 0


def test_shared_metrics_commit_is_the_summary_creation_boundary(
    hunter_db, monkeypatch, tmp_path
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    record_source_metrics(
        "Ashby",
        raw_jobs=4,
        eligible_jobs=0,
        inserted_jobs=0,
        duplicate_jobs=0,
        provider_used="Ashby",
        filter_summary={
            "run_id": "shared-finalizer",
            "raw_normalized": 4,
            "reject_role": 4,
            "request_count": 1,
            "errors": [],
        },
    )
    connection = database.get_connection()
    try:
        run = connection.execute(
            "SELECT completed_at,run_status FROM source_runs WHERE run_id='shared-finalizer'"
        ).fetchone()
        card = connection.execute(
            "SELECT logical_id,delivery_state FROM telegram_operational_outbox WHERE run_id='shared-finalizer'"
        ).fetchone()
    finally:
        connection.close()
    assert run["completed_at"]
    assert tuple(run)[1] == "completed"
    assert tuple(card) == ("adapter_run_summary:shared-finalizer", "pending")


def test_provider_emit_does_not_send_or_invent_uncommitted_run(
    hunter_db, monkeypatch, tmp_path, capsys
) -> None:
    _configure(hunter_db, monkeypatch, tmp_path)
    output = emit_source_run_result(
        {
            "source": "Ashby",
            "worker_action": "run",
            "success": True,
            "raw_jobs_found": 0,
        }
    )
    capsys.readouterr()
    assert output["source_run_notification"]["reason"] == "awaiting_canonical_run_commit"
    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM telegram_operational_outbox").fetchone()[0] == 0
    finally:
        connection.close()


def test_bootstrap_requires_terminal_and_zero_yield_summaries() -> None:
    root = Path(__file__).resolve().parent.parent
    bootstrap = json.loads((root / "config" / "bootstrap.json").read_text(encoding="utf-8"))
    policy = bootstrap["settings"]["source_run_notifications"]
    assert policy["enabled"] is True
    assert policy["terminal_summary_required"] is True
    assert policy["empty_result_alerts"] is True
    assert policy["cooldown_minutes"] == 0
    assert policy["cadence_skip_alerts"] is False
