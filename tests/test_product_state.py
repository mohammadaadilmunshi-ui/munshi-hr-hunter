from __future__ import annotations

from app.product_state import create_lane, fetch_jobs, lanes, save_volume_policy, set_job_state, tracker_status, volume_policy


def _job(connection, *, title: str = "People Operations Analyst") -> int:
    cursor = connection.execute("""INSERT INTO jobs(job_fingerprint,source,company_name,title,location_raw,description_raw,hunter_score)
        VALUES (?,?,?,?,?,?,?)""", (f"fingerprint-{title}", "Fixture", "Example Co", title, "New York, NY", "A documented role", 82))
    connection.commit()
    return int(cursor.lastrowid)


def test_save_skip_and_restore_are_persisted(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection(); job_id = _job(connection); connection.close()
    set_job_state(job_id, saved=True, skipped=True)
    visible, total = fetch_jobs(page_size=10)
    assert total == 0 and visible == []
    skipped, total = fetch_jobs(include_skipped=True, page_size=10)
    assert total == 1 and skipped[0]["saved"] == 1 and skipped[0]["skipped"] == 1
    set_job_state(job_id, skipped=False)
    visible, total = fetch_jobs(page_size=10)
    assert total == 1 and visible[0]["saved"] == 1


def test_job_filter_uses_parameters_and_exclusion(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection(); _job(connection, title="HR Analyst"); _job(connection, title="Payroll Specialist"); connection.close()
    rows, count = fetch_jobs(query="HR", page_size=10)
    assert count == 1 and rows[0]["title"] == "HR Analyst"
    rows, count = fetch_jobs(exclude="Payroll", page_size=10)
    assert count == 1 and rows[0]["title"] == "HR Analyst"


def test_new_lanes_are_disabled_and_unlimited_is_valid(hunter_db) -> None:
    create_lane("HR lane", {"keywords": "HR"}, 70, "unlimited", None)
    lane = lanes()[0]
    assert lane["enabled"] == 0 and lane["volume_mode"] == "unlimited"
    assert volume_policy()["mode"] == "unlimited"
    assert tracker_status("completed") != "Submitted"


def test_custom_volume_preference_is_persisted_for_dispatch(hunter_db) -> None:
    save_volume_policy("custom_limit", 17, True)
    assert volume_policy() == {"mode": "custom_limit", "daily_limit": 17, "review_first": True}
