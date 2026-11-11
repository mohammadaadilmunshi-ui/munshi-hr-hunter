from __future__ import annotations

from app.product_state import (
    activity_summary, create_lane, fetch_jobs, lanes, research_snapshot,
    save_review_preference, save_volume_policy, set_job_state, tracker_status, volume_policy,
)


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
    passed, total = fetch_jobs(result_set="passed", page_size=10)
    assert total == 1 and passed[0]["skipped"] == 1
    set_job_state(job_id, skipped=False)
    visible, total = fetch_jobs(page_size=10)
    assert total == 1 and visible[0]["saved"] == 1
    saved, total = fetch_jobs(result_set="saved", page_size=10)
    assert total == 1 and saved[0]["id"] == job_id


def test_job_filter_uses_parameters_and_exclusion(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection(); _job(connection, title="HR Analyst"); _job(connection, title="Payroll Specialist"); connection.close()
    rows, count = fetch_jobs(query="HR", page_size=10)
    assert count == 1 and rows[0]["title"] == "HR Analyst"
    rows, count = fetch_jobs(exclude="Payroll", page_size=10)
    assert count == 1 and rows[0]["title"] == "HR Analyst"


def test_job_search_scope_is_truthful_and_parameterized(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        job_id = _job(connection, title="Operations Analyst")
        connection.execute("UPDATE jobs SET company_name='Scope Co', description_raw='needle only in description' WHERE id=?", (job_id,))
        connection.commit()
    finally: connection.close()
    rows, count = fetch_jobs(query="needle", search_scope="title_description", page_size=10)
    assert count == 1 and rows[0]["title"] == "Operations Analyst"
    rows, count = fetch_jobs(query="needle", search_scope="title_company", page_size=10)
    assert count == 0 and rows == []
    rows, count = fetch_jobs(query="Scope", search_scope="title_company", page_size=10)
    assert count == 1 and rows[0]["company_name"] == "Scope Co"


def test_new_lanes_are_disabled_and_unlimited_is_valid(hunter_db) -> None:
    create_lane("HR lane", {"keywords": "HR"}, 70, "unlimited", None)
    lane = lanes()[0]
    assert lane["enabled"] == 0 and lane["volume_mode"] == "unlimited"
    assert volume_policy()["mode"] == "unlimited"
    assert tracker_status("completed") != "Submitted"


def test_custom_volume_preference_is_persisted_for_dispatch(hunter_db) -> None:
    save_volume_policy("custom_limit", 17, True)
    assert volume_policy() == {"mode": "custom_limit", "daily_limit": 17, "review_first": True}


def test_review_preference_does_not_activate_a_product_dispatch_policy(hunter_db) -> None:
    from app.database import get_setting
    save_review_preference(False)
    assert get_setting("product_automation_policy_v1", {}) == {}
    assert volume_policy()["review_first"] is False


def test_activity_and_research_summaries_use_stored_evidence_only(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        job_id = _job(connection, title="HR Research Analyst")
        connection.execute(
            """INSERT INTO n8n_results(job_id,job_fingerprint,send_mode,n8n_status,
                                           final_ats_score,completed_at)
               VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (job_id, "summary-result", "manual", "completed", 88),
        )
        connection.execute(
            "UPDATE jobs SET hard_rejection_reason='Work authorization evidence missing', work_authorization='Not recorded' WHERE id=?",
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()
    activity = activity_summary()
    snapshot = research_snapshot()
    assert activity["prepared_today"] == 1
    assert activity["submitted_today"] == 0
    assert snapshot["headline"]["jobs"] == 1
    assert snapshot["ats"]["scored_packages"] == 1
    assert snapshot["blockers"][0]["reason"] == "Work authorization evidence missing"
    assert snapshot["top_matches"][0]["title"] == "HR Research Analyst"
    assert snapshot["top_matches"][0]["hunter_score"] == 82
    assert snapshot["query_performance"] == []
