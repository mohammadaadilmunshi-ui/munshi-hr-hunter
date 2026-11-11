from __future__ import annotations

import json
import importlib
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import api, application_execution_artifact, database, telegram_sync, universal_n8n_progress
from app.job_store import save_job
from app.n8n_dispatch import build_payload, insert_queue_item
from app import n8n_dispatch
from app.phase67_common import safe_owned_job_snapshot, sha256_json


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


def _preflight_bound_callback_fixture() -> tuple[dict, dict, api.N8nStatusUpdate]:
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        job = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (stored["job_id"],)).fetchone())
        from app.application_preflight_package_v1 import ensure_schema as ensure_preflight_schema

        ensure_preflight_schema(connection)
        current_job = safe_owned_job_snapshot(
            int(job["id"]),
            connection=connection,
            tenant_id="default",
            user_id="local-owner",
        )
        job_digest = str(current_job["job_snapshot_sha256"])
        candidate = {
            "source_extraction_id": "extract-1",
            "profile_revision": 2,
            "profile_digest": "b" * 64,
        }
        snapshot = {
            "status": "READY_TO_APPLY",
            "application_id": "application-1",
            "job_snapshot_sha256": job_digest,
            "application_truth": {"candidate_profile_binding": candidate},
        }
        package_digest = sha256_json(snapshot)
        connection.execute(
            """INSERT INTO application_preflight_packages(
               package_id,tenant_id,user_id,job_id,application_id,idempotency_key,package_version,status,
               application_truth_projection_digest,job_snapshot_sha256,resume_version_id,resume_sha256,
               opportunity_evaluation_id,opportunity_result_sha256,snapshot_json,package_digest
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "preflight-1", "default", "local-owner", job["id"], "application-1", "fixture",
                "v1", "READY_TO_APPLY", "a" * 64, job_digest, "resume-1", "d" * 64,
                "opportunity-1", "e" * 64, json.dumps(snapshot), package_digest,
            ),
        )
        queue = insert_queue_item(
            connection, job, "telegram_manual", "production", preflight_package_id="preflight-1"
        )
        connection.execute("UPDATE n8n_dispatch_queue SET queue_status='accepted' WHERE id=?", (queue["id"],))
        connection.commit()
    finally:
        connection.close()
    payload = api.N8nStatusUpdate(
        row_id=int(job["id"]), job_fingerprint=str(job["job_fingerprint"]), n8n_status="completed",
        send_mode="telegram_manual", queue_id=int(queue["id"]), request_id=str(queue["request_id"]),
        application_id="application-1", preflight_package_id="preflight-1", artifact_id="artifact-1",
        version_id="version-1", version_number=1, filename="resume.pdf", mime_type="application/pdf",
        artifact_reference="hunter-n8n://fixture/artifact-1", artifact_sha256="a" * 64, page_count=1,
        candidate_truth_binding={
            "source_extraction_id": "extract-1", "profile_revision": 2, "profile_digest": "b" * 64,
        },
        job_snapshot_binding={"job_snapshot_sha256": job_digest},
        artifact_created_at="2026-09-07T00:00:00+00:00",
    )
    return job, queue, payload


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
    assert first["execution_artifact_id"] is None


def test_explicit_callback_disable_blocks_cloud_mutation(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("PRODUCTION_CALLBACKS_ENABLED", "false")
    payload = api.N8nStatusUpdate(
        row_id=1,
        job_fingerprint="blocked-callback",
        n8n_status="completed",
    )
    with pytest.raises(HTTPException) as exc_info:
        api.n8n_status_update(payload)
    assert exc_info.value.status_code == 503


def test_noncompleted_callback_never_claims_execution_artifact(hunter_db, monkeypatch) -> None:
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
    result = api.n8n_status_update(api.N8nStatusUpdate(
        row_id=int(job["id"]), job_fingerprint=str(job["job_fingerprint"]),
        n8n_status="running", send_mode="telegram_manual", queue_id=int(queue["id"]),
        request_id=str(queue["request_id"]), application_id="application-running-1",
    ))
    connection = database.get_connection()
    try:
        application_execution_artifact.ensure_schema(connection)
        artifacts = connection.execute(
            "SELECT COUNT(*) FROM application_execution_artifacts WHERE application_id='application-running-1'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert result["execution_artifact_id"] is None
    assert artifacts == 0


def test_preflight_bound_artifact_callback_is_exact_idempotent_and_conflicts_on_mutation(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(telegram_sync, "sync_latest_job_card", lambda *_a, **_k: {"success": True})
    freshness = {"fresh": True, "calls": 0}

    def _freshness(_package_id: str) -> dict[str, bool]:
        freshness["calls"] += 1
        # Regression proof: the real freshness path opens its own SQLite
        # connections. It must run before the callback takes BEGIN IMMEDIATE.
        probe = database.get_connection()
        try:
            n8n_dispatch.ensure_schema(probe)
            probe.commit()
        finally:
            probe.close()
        return {"fresh": bool(freshness["fresh"])}

    monkeypatch.setattr(
        "app.application_preflight_package_v1.preflight_package_freshness",
        _freshness,
    )
    job, queue, payload = _preflight_bound_callback_fixture()
    first = api.n8n_status_update(payload)

    # Once an exact callback has committed, an identical retry must remain
    # idempotent even if mutable preparation inputs become stale afterwards.
    freshness["fresh"] = False
    second = api.n8n_status_update(payload)
    assert second["duplicate_callback"] is True
    assert second["execution_artifact_id"] == first["execution_artifact_id"]
    assert freshness["calls"] == 1
    with pytest.raises(HTTPException) as conflict:
        api.n8n_status_update(payload.model_copy(update={"artifact_sha256": "d" * 64}))
    assert conflict.value.status_code == 409


def test_preflight_bound_callback_rejects_stale_inputs_before_receipt(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(telegram_sync, "sync_latest_job_card", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(
        "app.application_preflight_package_v1.preflight_package_freshness",
        lambda _package_id: {"fresh": False, "reasons": ["package_inputs_changed"]},
    )
    _job_row, _queue, payload = _preflight_bound_callback_fixture()
    with pytest.raises(HTTPException) as stale:
        api.n8n_status_update(payload)
    assert stale.value.status_code == 409
    connection = database.get_connection()
    try:
        receipts = connection.execute("SELECT COUNT(*) FROM n8n_callback_receipts").fetchone()[0]
        artifacts = connection.execute("SELECT COUNT(*) FROM application_execution_artifacts").fetchone()[0]
    finally:
        connection.close()
    assert receipts == 0
    assert artifacts == 0


def test_preflight_bound_callback_rejects_changed_job_snapshot_under_lock(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(telegram_sync, "sync_latest_job_card", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(
        "app.application_preflight_package_v1.preflight_package_freshness",
        lambda _package_id: {"fresh": True},
    )
    job, _queue, payload = _preflight_bound_callback_fixture()
    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET title='Changed after dispatch', updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(job["id"]),),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HTTPException) as stale:
        api.n8n_status_update(payload)
    assert stale.value.status_code == 409
    assert "job snapshot is stale" in str(stale.value.detail)


def test_preflight_bound_callback_rejects_corrupt_stored_package_digest(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(telegram_sync, "sync_latest_job_card", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(
        "app.application_preflight_package_v1.preflight_package_freshness",
        lambda _package_id: {"fresh": True},
    )
    _job_row, _queue, payload = _preflight_bound_callback_fixture()
    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT snapshot_json FROM application_preflight_packages WHERE package_id='preflight-1'"
        ).fetchone()
        corrupted = json.loads(str(row["snapshot_json"]))
        corrupted["application_id"] = "tampered-application"
        connection.execute(
            "UPDATE application_preflight_packages SET snapshot_json=? WHERE package_id='preflight-1'",
            (json.dumps(corrupted),),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HTTPException) as conflict:
        api.n8n_status_update(payload)
    assert conflict.value.status_code == 409
    assert "preflight package integrity" in str(conflict.value.detail)


def test_preflight_bound_callback_rolls_back_receipt_and_artifact_on_late_failure(
    hunter_db, monkeypatch
) -> None:
    _seed_contract()
    monkeypatch.setattr(telegram_sync, "sync_latest_job_card", lambda *_a, **_k: {"success": True})
    monkeypatch.setattr(
        "app.application_preflight_package_v1.preflight_package_freshness",
        lambda _package_id: {"fresh": True},
    )
    _job_row, queue, payload = _preflight_bound_callback_fixture()
    real_persist = universal_n8n_progress.persist_extended_result

    def _fail_after_result_write(connection, job_id, callback_payload, completed_at=None):
        real_persist(connection, job_id, callback_payload, completed_at)
        raise RuntimeError("forced late callback failure")

    monkeypatch.setattr(universal_n8n_progress, "persist_extended_result", _fail_after_result_write)
    with pytest.raises(HTTPException) as failed:
        api.n8n_status_update(payload)
    assert failed.value.status_code == 500

    connection = database.get_connection()
    try:
        assert connection.execute("SELECT COUNT(*) FROM n8n_callback_receipts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM application_execution_artifacts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM n8n_results").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='n8n_status_callback'"
        ).fetchone()[0] == 0
        status_row = connection.execute(
            "SELECT queue_status FROM n8n_dispatch_queue WHERE id=?",
            (int(queue["id"]),),
        ).fetchone()
    finally:
        connection.close()
    assert str(status_row["queue_status"]) == "accepted"


def test_migration_044_upgrades_legacy_queue_idempotently(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.execute("DROP TABLE n8n_dispatch_queue")
        connection.execute(
            """CREATE TABLE n8n_dispatch_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                job_fingerprint TEXT NOT NULL,
                dispatch_mode TEXT NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_id TEXT NOT NULL UNIQUE,
                queue_status TEXT NOT NULL DEFAULT 'pending',
                webhook_mode TEXT NOT NULL DEFAULT 'production',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                http_status INTEGER,
                response_text TEXT,
                last_error TEXT,
                queued_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                reserved_at TEXT,
                accepted_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            )"""
        )
        connection.commit()
        migration = importlib.import_module("migrations.044_n8n_preflight_callback_contract")
        migration.apply(connection)
        migration.apply(connection)
        connection.commit()
        queue_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(n8n_dispatch_queue)")
        }
        receipt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(n8n_callback_receipts)")
        }
        assert {
            "tenant_id", "user_id", "preflight_package_id", "preflight_package_digest",
            "application_id", "expected_job_snapshot_sha256", "expected_candidate_truth_json",
        } <= queue_columns
        assert {"payload_sha256", "execution_artifact_id"} <= receipt_columns
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert str(connection.execute("PRAGMA quick_check").fetchone()[0]).casefold() == "ok"
    finally:
        connection.close()


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
