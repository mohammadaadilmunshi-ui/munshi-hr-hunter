from __future__ import annotations

import json

from app import database
from app.job_store import save_job
from app.strict_dashboard_targeting_v2 import quarantine_unsent_adapter_jobs


def _eligible_job(**updates):
    job = {
        "source": "Fixture ATS",
        "source_tier": 1,
        "ats_job_id": "REQ-100",
        "company_name": "Example Employer",
        "title": "People Analytics Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "apply_url": "https://example.test/jobs/req-100",
        "job_url": "https://example.test/jobs/req-100",
        "description_raw": "Analyze workforce data and build dashboards.",
        "status": "found",
        "entry_path": "adapter_discovery",
    }
    job.update(updates)
    return job


def test_setting_history_is_versioned_and_noop_is_not_duplicated(hunter_db) -> None:
    value = {"enabled": True, "limit": 4}
    database.save_setting("fixture_setting", value, changed_by="pytest:Aadil")
    database.save_setting("fixture_setting", value, changed_by="pytest:Aadil")
    connection = database.get_connection()
    try:
        rows = connection.execute(
            "SELECT * FROM configuration_history WHERE setting_key='fixture_setting'"
        ).fetchall()
    finally:
        connection.close()
    assert len(rows) == 1
    assert rows[0]["changed_by"] == "pytest:Aadil"
    assert rows[0]["new_hash"]


def test_registry_controls_write_versioned_configuration_history(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode)
            VALUES ('Fixture Source',1,0,60,'free')
            """
        )
        source = connection.execute(
            "SELECT source_name,enabled FROM source_health ORDER BY source_name LIMIT 1"
        ).fetchone()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS provider_board_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT NOT NULL,
                provider TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                priority_weight INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        board_id = connection.execute(
            "INSERT INTO provider_board_registry(company_name,provider) VALUES ('Fixture Co','Fixture ATS')"
        ).lastrowid
        connection.commit()
    finally:
        connection.close()

    source_changed = database.save_source_policy(
        str(source["source_name"]),
        enabled=not bool(source["enabled"]),
        cadence_minutes=333,
        changed_by="pytest:owner",
    )
    source_noop = database.save_source_policy(
        str(source["source_name"]),
        enabled=not bool(source["enabled"]),
        cadence_minutes=333,
        changed_by="pytest:owner",
    )
    board_changed = database.save_board_policy(
        int(board_id),
        enabled=True,
        priority_weight=17,
        notes="Representative U.S. fixture board",
        changed_by="pytest:owner",
    )
    board_noop = database.save_board_policy(
        int(board_id),
        enabled=True,
        priority_weight=17,
        notes="Representative U.S. fixture board",
        changed_by="pytest:owner",
    )
    connection = database.get_connection()
    try:
        keys = [
            row[0]
            for row in connection.execute(
                "SELECT setting_key FROM configuration_history ORDER BY id"
            ).fetchall()
        ]
    finally:
        connection.close()
    assert source_changed is True and source_noop is False
    assert board_changed is True and board_noop is False
    assert keys.count(f"source_registry:{source['source_name']}") == 1
    assert keys.count(f"provider_board_registry:{board_id}") == 1


def test_source_policy_controls_scheduler_state(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.executescript(
            """
            DELETE FROM source_random_schedule;
            INSERT INTO source_health(
                source_name,source_tier,enabled,cadence_minutes,cost_mode
            ) VALUES ('Fixture Scheduled Source',1,1,60,'free');
            INSERT INTO source_random_schedule(
                source_name,next_run_at,base_cadence_minutes,
                schedule_reason,schedule_state
            ) VALUES (
                'Fixture Scheduled Source',CURRENT_TIMESTAMP,60,
                'fixture_ready','ready'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    database.save_source_policy(
        "Fixture Scheduled Source",
        enabled=False,
        cadence_minutes=180,
        changed_by="pytest:owner",
    )
    connection = database.get_connection()
    try:
        disabled = connection.execute(
            "SELECT * FROM source_random_schedule WHERE source_name=?",
            ("Fixture Scheduled Source",),
        ).fetchone()
    finally:
        connection.close()
    assert disabled["schedule_state"] == "disabled"
    assert disabled["next_run_at"] is None
    assert disabled["base_cadence_minutes"] == 180

    database.save_source_policy(
        "Fixture Scheduled Source",
        enabled=True,
        cadence_minutes=240,
        changed_by="pytest:owner",
    )
    connection = database.get_connection()
    try:
        enabled = connection.execute(
            "SELECT * FROM source_random_schedule WHERE source_name=?",
            ("Fixture Scheduled Source",),
        ).fetchone()
    finally:
        connection.close()
    assert enabled["schedule_state"] == "ready"
    assert enabled["next_run_at"]
    assert enabled["base_cadence_minutes"] == 240


def test_job_store_targets_once_then_records_global_duplicate(hunter_db) -> None:
    connection = database.get_connection()
    try:
        first = save_job(connection, _eligible_job(), actor="fixture_worker")
        second = save_job(
            connection,
            _eligible_job(source="Second Fixture ATS", apply_url="https://mirror.test/jobs/req-100"),
            actor="fixture_worker",
        )
        connection.commit()
        stored = connection.execute("SELECT * FROM jobs WHERE id = ?", (first["job_id"],)).fetchone()
        decisions = connection.execute(
            "SELECT primary_category FROM targeting_decisions ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    assert first["inserted"] is True
    assert first["primary_category"] == "ELIGIBLE"
    assert second["inserted"] is False
    assert second["primary_category"] == "DUPLICATE"
    assert [row["primary_category"] for row in decisions] == ["ELIGIBLE", "DUPLICATE"]
    assert stored["primary_decision"] == "ELIGIBLE"
    assert stored["targeting_rules_hash"]
    assert json.loads(stored["role_evidence_json"])["accepted"] is True
    assert len(json.loads(stored["source_provenance_json"])) == 2


def test_rejected_job_never_reaches_jobs_table(hunter_db) -> None:
    connection = database.get_connection()
    try:
        result = save_job(
            connection,
            _eligible_job(title="Patient Recruitment Coordinator"),
            actor="fixture_worker",
        )
        connection.commit()
        job_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        decision = connection.execute(
            "SELECT primary_category FROM targeting_decisions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert result["status"] == "rejected_by_dashboard_targeting"
    assert job_count == 0
    assert decision == "REJECT_ROLE"


def test_quarantine_syncs_canonical_rejection_state(hunter_db) -> None:
    connection = database.get_connection()
    try:
        stored = save_job(connection, _eligible_job(), actor="fixture_worker")
        connection.execute(
            """
            UPDATE jobs
            SET title='Patient Recruitment Coordinator',
                description_raw='Recruit patients for clinical research trials.',
                primary_decision='ELIGIBLE',
                telegram_sent=0,
                status='found',
                hunter_score=99,
                match_label='GREAT_MATCH'
            WHERE id=?
            """,
            (stored["job_id"],),
        )
        connection.commit()
    finally:
        connection.close()

    result = quarantine_unsent_adapter_jobs(source_prefix="Fixture ATS")

    connection = database.get_connection()
    try:
        row = connection.execute(
            """
            SELECT telegram_sent,status,primary_decision,decision_evidence_json,
                   role_evidence_json,targeting_rules_hash,hunter_score,match_label
            FROM jobs WHERE id=?
            """,
            (stored["job_id"],),
        ).fetchone()
    finally:
        connection.close()

    assert result["quarantined"] == 1
    assert row["telegram_sent"] == -1
    assert row["status"] == "rejected_by_dashboard_targeting"
    assert row["primary_decision"] == "REJECT_ROLE"
    assert json.loads(row["decision_evidence_json"])["quarantined_before_telegram"] is True
    assert json.loads(row["role_evidence_json"])["accepted"] is False
    assert row["targeting_rules_hash"]
    assert row["hunter_score"] == 0
    assert row["match_label"] == "REJECTED"
