from __future__ import annotations

from app.product_shell import _resolved_subroute_state
from app.product_state import fetch_jobs, research_snapshot, set_job_state


def _job(
    connection,
    *,
    title: str,
    score: float = 80,
    rejection: str | None = None,
) -> int:
    cursor = connection.execute(
        """INSERT INTO jobs(
               job_fingerprint,
               source,
               company_name,
               title,
               location_raw,
               description_raw,
               hunter_score,
               hard_rejection_reason
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            f"sol-remainder-{title}",
            "Fixture",
            "Evidence Co",
            title,
            "New York, NY",
            "Evidence-backed fixture",
            score,
            rejection,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)


def test_product_subroute_mapping_matches_current_ui_labels() -> None:
    assert _resolved_subroute_state("tracker", tab="pipeline") == (
        "product_tracker_tab",
        "Pipeline",
    )
    assert _resolved_subroute_state("tracker", tab="inbox") == (
        "product_tracker_tab",
        "Inbox",
    )
    assert _resolved_subroute_state("profile", tab="cover-letter") == (
        "product_profile_tab",
        "Cover letters",
    )
    assert _resolved_subroute_state("profile", tab="details") == (
        "product_profile_tab",
        "Profile details",
    )

    expected_settings = {
        "apply": "Apply",
        "automation": "Automation",
        "integrations": "Integrations",
        "profile": "Profile & defaults",
        "credentials": "Credentials",
        "advanced": "Advanced / System",
    }
    for query_value, label in expected_settings.items():
        assert _resolved_subroute_state(
            "settings",
            section=query_value,
        ) == ("product_settings_section", label)

    assert _resolved_subroute_state("settings", section="unknown") is None


def test_result_sets_keep_passed_jobs_recoverable(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        passed_id = _job(connection, title="Passed HR Analyst", score=81)
        saved_id = _job(connection, title="Saved People Analyst", score=82)
    finally:
        connection.close()

    set_job_state(passed_id, skipped=True)
    set_job_state(saved_id, saved=True)

    passed, passed_count = fetch_jobs(result_set="passed", page_size=10)
    saved, saved_count = fetch_jobs(result_set="saved", page_size=10)

    assert passed_count == 1
    assert passed[0]["id"] == passed_id
    assert passed[0]["skipped"] == 1

    assert saved_count == 1
    assert saved[0]["id"] == saved_id
    assert saved[0]["saved"] == 1


def test_unknown_result_set_falls_back_to_normal_visible_jobs(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        visible_id = _job(connection, title="Visible HR Analyst", score=80)
        passed_id = _job(connection, title="Hidden Passed Analyst", score=79)
    finally:
        connection.close()

    set_job_state(passed_id, skipped=True)
    rows, count = fetch_jobs(result_set="not-a-real-view", page_size=10)

    assert count == 1
    assert rows[0]["id"] == visible_id


def test_research_keeps_score_and_blocker_evidence_together(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        accepted_id = _job(connection, title="Accepted HR Analyst", score=91)
        blocked_id = _job(
            connection,
            title="Blocked HR Director",
            score=99,
            rejection="seniority_not_targeted",
        )
    finally:
        connection.close()

    snapshot = research_snapshot()
    by_id = {int(row["id"]): row for row in snapshot["top_matches"]}

    assert accepted_id in by_id
    assert blocked_id in by_id
    assert by_id[blocked_id]["hard_rejection_reason"] == "seniority_not_targeted"
