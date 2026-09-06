from __future__ import annotations

from uuid import uuid4

from app import database
from app import jd_intelligence_v1 as jd
from app.tenant_foundation import associate_owned_record


def _job(
    *,
    description: str = (
        "Join our People team. Must be authorized to work in the United States. "
        "We are unable to provide visa sponsorship."
    ),
    responsibilities: str = (
        "Build workforce dashboards.\n"
        "Maintain accurate employee records.\n"
        "Partner with HR stakeholders."
    ),
    qualifications: str = (
        "3+ years of HR operations experience required.\n"
        "Bachelor's degree required.\n"
        "Advanced Excel required."
    ),
    preferred_qualifications: str = "Workday experience preferred.",
    preferred_skills: str = "Power BI preferred.",
    skills_keywords: str = "Excel, Power BI, HRIS",
    work_authorization: str = "Must be authorized to work in the United States",
    salary_raw: str = "$80,000 - $95,000",
) -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,location_raw,city,state,country,
                remote_type,description_raw,salary_raw,target_track,employment_type,
                responsibilities,qualifications,preferred_qualifications,preferred_skills,
                skills_keywords,work_authorization,hunter_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"stage-b-{uuid4()}",
                "fixture",
                "Example Co",
                "People Analytics Analyst",
                "New York, NY",
                "New York",
                "NY",
                "US",
                "Hybrid",
                description,
                salary_raw,
                "People Analytics",
                "Full-time",
                responsibilities,
                qualifications,
                preferred_qualifications,
                preferred_skills,
                skills_keywords,
                work_authorization,
                90,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _by_text(snapshot: dict, needle: str) -> dict:
    return next(
        item
        for item in snapshot["requirements"]
        if needle.casefold() in item["exact_text"].casefold()
    )


def test_stage_b_same_job_is_content_deterministic(hunter_db) -> None:
    job_id = _job()
    first = jd.analyze_job(job_id, persist=False)
    second = jd.analyze_job(job_id, persist=False)

    assert first["snapshot_digest"] == second["snapshot_digest"]
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["job_snapshot_sha256"] == second["job_snapshot_sha256"]
    assert first["diagnostics"]["llm_calls"] == 0
    assert first["submission_authority"] is False


def test_stage_b_required_and_preferred_are_not_conflated(hunter_db) -> None:
    snapshot = jd.analyze_job(_job(), persist=False)

    required_excel = _by_text(snapshot, "Advanced Excel required")
    preferred_workday = _by_text(snapshot, "Workday experience preferred")
    preferred_power_bi = _by_text(snapshot, "Power BI preferred")

    assert required_excel["priority"] == "MUST_HAVE"
    assert preferred_workday["priority"] == "PREFERRED"
    assert preferred_power_bi["priority"] == "PREFERRED"
    assert preferred_workday["type"] == "TOOL"


def test_stage_b_requirements_are_grounded_to_canonical_source_fields(hunter_db) -> None:
    job_id = _job()
    snapshot = jd.analyze_job(job_id, persist=False)
    raw_job = jd.safe_owned_job_snapshot(job_id)["job"]

    assert snapshot["requirements"]
    assert len({item["requirement_id"] for item in snapshot["requirements"]}) == len(
        snapshot["requirements"]
    )
    for requirement in snapshot["requirements"]:
        source = str(raw_job.get(requirement["source_field"]) or "")
        assert requirement["exact_text"] in source
        assert requirement["source_start"] >= 0
        assert requirement["source_end"] >= requirement["source_start"]


def test_stage_b_keeps_authorization_and_sponsorship_as_employer_requirements(hunter_db) -> None:
    snapshot = jd.analyze_job(_job(), persist=False)

    auth = [
        item
        for item in snapshot["requirements"]
        if item["type"] == "WORK_AUTHORIZATION"
    ]
    sponsorship = [
        item for item in snapshot["requirements"] if item["type"] == "SPONSORSHIP"
    ]

    assert auth
    assert sponsorship
    assert all(item["priority"] in {"MUST_HAVE", "UNKNOWN"} for item in auth)
    assert snapshot["submission_authority"] is False


def test_stage_b_salary_without_cadence_remains_unknown(hunter_db) -> None:
    snapshot = jd.analyze_job(_job(salary_raw="$80,000 - $95,000"), persist=False)
    salary = _by_text(snapshot, "$80,000 - $95,000")

    assert salary["type"] == "COMPENSATION"
    assert salary["structured_constraints"]["compensation"]["currency"] == "USD"
    assert salary["structured_constraints"]["compensation"]["cadence"] is None
    assert "compensation_cadence" in snapshot["unknowns"]


def test_stage_b_persistence_is_idempotent_and_stales_after_job_change(hunter_db) -> None:
    job_id = _job()
    first = jd.analyze_job(job_id)
    second = jd.analyze_job(job_id)

    assert first["snapshot_id"] == second["snapshot_id"]
    assert jd.snapshot_freshness(first["snapshot_id"])["fresh"] is True

    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET qualifications=? WHERE id=?",
            ("5+ years of HR operations experience required.", job_id),
        )
        connection.commit()
    finally:
        connection.close()

    freshness = jd.snapshot_freshness(first["snapshot_id"])
    assert freshness["fresh"] is False

    third = jd.analyze_job(job_id)
    assert third["snapshot_id"] != first["snapshot_id"]
    assert third["job_snapshot_sha256"] != first["job_snapshot_sha256"]


def test_stage_b_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.035_stage_b_jd_intelligence_v1",
            fromlist=["apply"],
        )
        migration.apply(connection)
        connection.commit()
        after = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()

    assert before <= after
    assert "jobs" in after
    assert "jd_intelligence_snapshots" in after
