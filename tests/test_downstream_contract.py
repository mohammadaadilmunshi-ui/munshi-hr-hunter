from __future__ import annotations

import json
from pathlib import Path

from app import api, database, telegram_sync
from app.job_store import save_job
from app.n8n_dispatch import build_payload, insert_queue_item
from app import n8n_dispatch


ROOT = Path(__file__).resolve().parent.parent


def _seed_contract() -> dict:
    contract = json.loads((ROOT / "config" / "downstream_contract.json").read_text())
    database.save_setting("downstream_contract", contract, changed_by="pytest")
    database.save_setting(
        "scoring",
        {
            "auto_n8n_threshold": 97,
            "daily_auto_n8n_limit": 7,
            "daily_manual_n8n_limit": 25,
        },
        changed_by="pytest",
    )
    return contract


def _job() -> dict:
    return {
        "source": "Fixture ATS",
        "source_tier": 1,
        "ats_job_id": "REQ-N8N-1",
        "company_name": "Example Employer",
        "title": "People Analytics Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "apply_url": "https://example.test/n8n-contract",
        "description_raw": "Analyze workforce metrics and maintain HR dashboards.",
        "entry_path": "adapter_discovery",
    }


def test_payload_uses_canonical_contract_and_queue_is_idempotent(hunter_db) -> None:
    contract = _seed_contract()
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        connection.execute(
            "UPDATE jobs SET status='approved_for_n8n' WHERE id=?", (stored["job_id"],)
        )
        job = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (stored["job_id"],)).fetchone())
        first = insert_queue_item(connection, job, "telegram_manual", "production")
        second = insert_queue_item(connection, job, "telegram_manual", "production")
        connection.commit()
    finally:
        connection.close()

    assert first["id"] == second["id"]
    payload = build_payload(job, first, webhook_mode="production")
    required = {
        "row_id", "hunter_row_id", "job_fingerprint", "idempotency_key",
        "request_id", "queue_id", "manual_job_text", "full_job_description",
        "callback_url", "schema_version", "queue_version", "source_adapter",
        "ats_engine_required", "ats_final_gate_required", "ats_target_score",
    }
    assert required <= set(payload)
    assert payload["callback_url"] == contract["callback_url"]
    assert payload["queue_version"] == contract["queue_version"]
    assert payload["ats_target_score"] == contract["ats_target_score"]
    assert payload["test_mode"] is False


def test_duplicate_callback_is_acknowledged_without_replayed_side_effects(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(
        telegram_sync,
        "sync_latest_job_card",
        lambda *_args, **_kwargs: {"success": True, "test": True},
    )
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        job = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (stored["job_id"],)).fetchone())
        queue = insert_queue_item(connection, job, "telegram_manual", "production")
        connection.commit()
    finally:
        connection.close()
    payload = api.N8nStatusUpdate(
        row_id=int(job["id"]),
        job_fingerprint=str(job["job_fingerprint"]),
        n8n_status="completed",
        send_mode="telegram_manual",
        queue_id=int(queue["id"]),
        request_id=str(queue["request_id"]),
    )
    first = api.n8n_status_update(payload)
    second = api.n8n_status_update(payload)
    connection = database.get_connection()
    try:
        receipts = connection.execute("SELECT COUNT(*) FROM n8n_callback_receipts").fetchone()[0]
        results = connection.execute("SELECT COUNT(*) FROM n8n_results").fetchone()[0]
        events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='n8n_status_callback'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert first["success"] is True
    assert second["duplicate_callback"] is True
    assert receipts == 1
    assert results == 1
    assert events == 1


def test_unavailable_n8n_is_recorded_without_losing_queue_item(hunter_db, monkeypatch) -> None:
    _seed_contract()
    database.save_setting("runtime", {"n8n_enabled": True}, changed_by="pytest")
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        job = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (stored["job_id"],)).fetchone())
        queue = insert_queue_item(connection, job, "telegram_manual", "production")
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(n8n_dispatch, "webhook_url", lambda _mode: "http://127.0.0.1:1/fixture")

    def unavailable(*_args, **_kwargs):
        raise ConnectionError("fixture n8n unavailable")

    monkeypatch.setattr(n8n_dispatch.requests, "post", unavailable)
    result = n8n_dispatch.dispatch_pending(
        webhook_mode="production", dry_run=False, allow_disabled=False, limit=1
    )
    connection = database.get_connection()
    try:
        stored_queue = connection.execute(
            "SELECT queue_status,attempt_count,last_error FROM n8n_dispatch_queue WHERE id=?",
            (queue["id"],),
        ).fetchone()
        failed_events = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='n8n_dispatch_failed'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert result["success"] is False
    assert result["dispatch_status"] == "not_dispatched"
    assert result["n8n_calls"] == 0
    assert stored_queue["queue_status"] == "failed"
    assert stored_queue["attempt_count"] == 1
    assert "fixture n8n unavailable" in stored_queue["last_error"]
    assert failed_events == 1


def test_completed_result_prevents_automatic_duplicate_dispatch(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    database.save_setting("runtime", {"n8n_enabled": True}, changed_by="pytest")
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        job_id = int(stored["job_id"])
        connection.execute(
            "UPDATE jobs SET status='approved_for_n8n', sent_to_n8n=0 WHERE id=?",
            (job_id,),
        )
        job = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        connection.execute(
            """
            INSERT INTO n8n_results (
              job_id,job_fingerprint,send_mode,n8n_status,completed_at
            ) VALUES (?,?,?,'completed_without_writer',CURRENT_TIMESTAMP)
            """,
            (job_id, job["job_fingerprint"], "telegram_manual"),
        )
        connection.commit()
        assert n8n_dispatch.plan_candidates(connection)["manual_candidates"] == []
        queue = insert_queue_item(connection, job, "telegram_manual", "production")
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(n8n_dispatch, "webhook_url", lambda _mode: "https://example.test")

    def must_not_dispatch(*_args, **_kwargs):
        raise AssertionError("completed job was dispatched twice")

    monkeypatch.setattr(n8n_dispatch.requests, "post", must_not_dispatch)
    result = n8n_dispatch.dispatch_pending(
        webhook_mode="production", dry_run=False, allow_disabled=False, limit=1
    )
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT queue_status,last_error FROM n8n_dispatch_queue WHERE id=?",
            (queue["id"],),
        ).fetchone()
    finally:
        connection.close()
    assert result["success"] is True
    assert result["dispatch_status"] == "duplicate_suppressed"
    assert result["n8n_calls"] == 0
    assert row["queue_status"] == "cancelled"
    assert row["last_error"] == "completed_result_already_exists"


def test_auto_dispatch_revalidates_current_canonical_targeting(hunter_db) -> None:
    _seed_contract()
    connection = database.get_connection()
    try:
        rejected = save_job(
            connection,
            {
                **_job(),
                "ats_job_id": "REQ-N8N-LEGACY-REJECT",
                "apply_url": "https://example.test/n8n-legacy-reject",
            },
            actor="fixture_worker",
        )
        eligible = save_job(
            connection,
            {
                **_job(),
                "ats_job_id": "REQ-N8N-CURRENT-ELIGIBLE",
                "company_name": "Second Example Employer",
                "title": "HR Operations Analyst",
                "apply_url": "https://example.test/n8n-current-eligible",
            },
            actor="fixture_worker",
        )
        connection.execute(
            """
            UPDATE jobs
            SET title = CASE WHEN id=? THEN 'Patient Recruitment Coordinator' ELSE title END,
                description_raw = CASE WHEN id=?
                    THEN 'Coordinate recruitment for clinical study patients.'
                    ELSE description_raw END,
                status='found', hunter_score=100, hard_rejection_reason=NULL,
                sent_to_n8n=0, already_applied=0, cpt_trapdoor=0
            WHERE id IN (?, ?)
            """,
            (
                rejected["job_id"],
                rejected["job_id"],
                rejected["job_id"],
                eligible["job_id"],
            ),
        )
        connection.commit()

        plan = n8n_dispatch.plan_candidates(connection)
    finally:
        connection.close()

    candidate_ids = {int(job["id"]) for job in plan["auto_candidates"]}
    assert int(rejected["job_id"]) not in candidate_ids
    assert candidate_ids == {int(eligible["job_id"])}
