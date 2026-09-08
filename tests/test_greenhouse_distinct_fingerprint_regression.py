from __future__ import annotations

from app import database
from app.job_store import save_job


def _job(**updates):
    value = {
        "source": "Greenhouse",
        "source_tier": 1,
        "ats_job_id": "REQ-GH-90001",
        "company_name": "Example Employer, Inc.",
        "title": "People Analytics Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "apply_url": "https://boards.greenhouse.io/example/jobs/90001",
        "description_raw": "Analyze workforce metrics and maintain HR dashboards.",
        "entry_path": "adapter_discovery",
    }
    value.update(updates)
    return value


def test_repeated_nonkeeper_distinct_fingerprint_does_not_violate_unique_constraint(hunter_db) -> None:
    connection = database.get_connection()
    corrected = _job(
        source="Greenhouse",
        ats_job_id="REQ-GH-90001-CORRECTED",
        apply_url="https://boards.greenhouse.io/example/jobs/90001-corrected",
    )

    try:
        first = save_job(connection, _job(), actor="fixture_worker")
        connection.execute(
            "UPDATE jobs SET status='rejected' WHERE id=?",
            (first["job_id"],),
        )

        second = save_job(connection, corrected, actor="fixture_worker")
        connection.execute(
            "UPDATE jobs SET status='rejected' WHERE id=?",
            (second["job_id"],),
        )

        # Before the fix, this third identical corrected rediscovery recreates
        # the same deterministic :distinct: fingerprint and crashes with:
        # sqlite3.IntegrityError: UNIQUE constraint failed: jobs.job_fingerprint
        third = save_job(connection, corrected, actor="fixture_worker")
        connection.commit()

        fingerprints = {
            int(row["id"]): str(row["job_fingerprint"])
            for row in connection.execute(
                "SELECT id, job_fingerprint FROM jobs WHERE id IN (?, ?, ?)",
                (first["job_id"], second["job_id"], third["job_id"]),
            ).fetchall()
        }
    finally:
        connection.close()

    assert first["inserted"] is True
    assert second["inserted"] is True
    assert third["inserted"] is True
    assert len({first["job_id"], second["job_id"], third["job_id"]}) == 3
    assert fingerprints[second["job_id"]] != fingerprints[third["job_id"]]
    assert ":distinct:" in fingerprints[second["job_id"]]
    assert ":retry:1" in fingerprints[third["job_id"]]
