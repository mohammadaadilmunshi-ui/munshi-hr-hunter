from __future__ import annotations

from app.product_state import clear_master_resume, fetch_jobs, master_resume, research_snapshot, save_master_resume, tracker_status


def _job(connection, *, title: str, company: str = "Evidence Co", target_track: str = "People Analytics", score: float = 82, blocker: str | None = None) -> int:
    cursor = connection.execute(
        """INSERT INTO jobs(job_fingerprint,source,company_name,title,location_raw,description_raw,hunter_score,target_track,hard_rejection_reason,first_seen_at)
           VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (f"v21-{title}-{company}", "Fixture", company, title, "New York, NY", "Evidence-backed analytics and recruiting description", score, target_track, blocker),
    )
    connection.commit()
    return int(cursor.lastrowid)


def test_all_fields_search_includes_company_and_multi_exclusion(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        _job(connection, title="People Analyst", company="Needle Corporation")
        _job(connection, title="Payroll Specialist", company="Other Co")
    finally:
        connection.close()
    rows, count = fetch_jobs(query="Needle", search_scope="all_fields", page_size=10)
    assert count == 1 and rows[0]["company_name"] == "Needle Corporation"
    rows, count = fetch_jobs(exclude="Payroll, Needle", page_size=10)
    assert count == 0 and rows == []


def test_advanced_target_eligibility_and_score_filters(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        _job(connection, title="Eligible People Analyst", target_track="People Analytics", score=91)
        _job(connection, title="Blocked HR Manager", target_track="HR Operations", score=75, blocker="hard_reject_keyword")
    finally:
        connection.close()
    rows, count = fetch_jobs(target_track="People Analytics", eligibility="unblocked", minimum_score=90, maximum_score=100, page_size=10)
    assert count == 1 and rows[0]["title"] == "Eligible People Analyst"
    rows, count = fetch_jobs(eligibility="blocked", maximum_score=80, page_size=10)
    assert count == 1 and rows[0]["title"] == "Blocked HR Manager"


def test_tracker_statuses_are_genuine_not_other() -> None:
    assert tracker_status("truth_review_required") == "Needs review"
    assert tracker_status("final_ready_deterministic_95_plus") == "Prepared"
    assert tracker_status("rejected_by_dashboard_targeting") == "Blocked"
    assert tracker_status("unexpected_new_state") == "Workflow: Unexpected New State"
    assert tracker_status(None, None) == "Status not recorded"


def test_master_resume_requires_explicit_designation(hunter_db) -> None:
    assert master_resume() == {}
    save_master_resume(42, "https://example.com/master.pdf", "Candidate-selected master resume")
    record = master_resume()
    assert record["job_id"] == 42 and record["url"] == "https://example.com/master.pdf"
    clear_master_resume()
    assert master_resume() == {}


def test_research_contains_lifetime_discovery_evidence(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        _job(connection, title="Research Analyst")
    finally:
        connection.close()
    snapshot = research_snapshot()
    assert "lifetime" in snapshot and "scanned" in snapshot["lifetime"]
    assert "source_telemetry" in snapshot
