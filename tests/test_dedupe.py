from __future__ import annotations

from app import database
from app.job_duplicate_guard import fingerprints, register_job
from app.job_store import save_job


def _job(**updates):
    value = {
        "source": "Workday/Fixture",
        "source_tier": 1,
        "ats_job_id": "REQ-90001",
        "company_name": "Example Employer, Inc.",
        "title": "People Analytics Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "apply_url": "https://example.test/jobs/req-90001?utm_source=workday",
        "description_raw": "Analyze workforce metrics and maintain HR dashboards.",
        "entry_path": "adapter_discovery",
    }
    value.update(updates)
    return value


def _shared_kinds(left: dict, right: dict) -> set[str]:
    left_values = set(fingerprints(left))
    right_values = set(fingerprints(right))
    return {kind for kind, _value in left_values & right_values}


def test_cross_source_identity_contract() -> None:
    base = _job()
    different_url = _job(
        source="LinkedIn/JobSpy",
        apply_url="https://linkedin.example/jobs/view/90001?trk=feed",
    )
    same_requisition_other_provider = _job(
        source="Indeed/JobSpy",
        apply_url="https://indeed.example/viewjob?jk=90001",
    )
    changed_description_repost = _job(
        ats_job_id="REQ-REPOST-2",
        apply_url="https://example.test/jobs/repost-2",
        description_raw="Updated responsibilities with the same role and location.",
    )
    different_level = _job(
        ats_job_id="REQ-SENIOR-2",
        title="Senior People Analytics Analyst",
        apply_url="https://example.test/jobs/senior-2",
    )
    different_location = _job(
        ats_job_id="REQ-NYC-2",
        location_raw="New York, NY",
        state="NY",
        apply_url="https://example.test/jobs/nyc-2",
    )

    assert "semantic" in _shared_kinds(base, different_url)
    assert "ats" in _shared_kinds(base, same_requisition_other_provider)
    assert "semantic" in _shared_kinds(base, changed_description_repost)
    assert not _shared_kinds(base, different_level)
    assert not _shared_kinds(base, different_location)


def test_historical_rejected_keeper_does_not_suppress_corrected_rediscovery(hunter_db) -> None:
    connection = database.get_connection()
    try:
        first = save_job(connection, _job(), actor="fixture_worker")
        connection.execute("UPDATE jobs SET status='rejected' WHERE id=?", (first["job_id"],))
        register_job(connection, int(first["job_id"]), _job())
        second = save_job(
            connection,
            _job(source="LinkedIn/JobSpy", apply_url="https://mirror.test/jobs/90001"),
            actor="fixture_worker",
        )
        connection.commit()
    finally:
        connection.close()
    assert first["inserted"] is True
    assert second["inserted"] is True
    assert second["job_id"] != first["job_id"]
