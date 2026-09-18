from __future__ import annotations

from app.manual_input import persist_manual_job


def test_confirmed_manual_job_uses_canonical_store_without_queueing(hunter_db) -> None:
    raw = """Job Title: HR Operations Analyst
Company Name: Example Employer
Location: Austin, TX
Application: https://example.test/jobs/manual-product
Job Description: Support human resources operations and workforce reporting.
"""
    result = persist_manual_job(raw)
    assert result["success"] is True and result["job_id"] > 0
    from app.database import get_connection
    connection = get_connection()
    try:
        job = connection.execute("SELECT source,manual_job_text FROM jobs WHERE id=?", (result["job_id"],)).fetchone()
        queued = connection.execute("SELECT COUNT(*) FROM n8n_dispatch_queue WHERE job_id=?", (result["job_id"],)).fetchone()[0]
    finally: connection.close()
    assert job["source"] == "Telegram Manual Input"
    assert job["manual_job_text"]
    assert queued == 0


def test_manual_job_refuses_missing_labeled_fields(hunter_db) -> None:
    result = persist_manual_job("Application: https://example.test/jobs/missing\nJob Description: A role")
    assert result["success"] is False
    assert {"Job Title", "Company Name", "Location"} <= set(result["missing_fields"])
