from __future__ import annotations

import sqlite3
import threading
import time

from app.manual_input import analyze_job_url, persist_analyzed_job, persist_manual_job


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


def test_greenhouse_url_analyzes_and_persists_duplicate_safe(hunter_db) -> None:
    url = "https://job-boards.greenhouse.io/oklo/jobs/6192531004?gh_src=test"

    def fetcher(**kwargs):
        assert kwargs["board_token"] == "oklo"
        return {
            "jobs": [
                {
                    "ats_job_id": "6192531004",
                    "source": "Greenhouse/oklo",
                    "company_name": "Oklo",
                        "title": "People Operations Associate",
                        "location_raw": "Santa Clara, CA",
                        "city": "Santa Clara",
                        "state": "CA",
                        "country": "US",
                    "remote_type": "Hybrid",
                    "employment_type": "Full-time",
                    "description_raw": "Support people operations and employee programs.",
                    "job_url": url,
                    "apply_url": url,
                }
            ]
        }

    analyzed = analyze_job_url(url, greenhouse_fetcher=fetcher)
    assert analyzed["supported"] is True
    assert analyzed["missing"] == []
    assert analyzed["job"]["title"] == "People Operations Associate"

    first = persist_analyzed_job(analyzed["job"])
    second = persist_analyzed_job(analyzed["job"])
    assert first["success"] is True and first["inserted"] is True
    assert second["success"] is True and second["inserted"] is False
    assert second["job_id"] == first["job_id"]


def test_manual_job_survives_transient_sqlite_writer_contention(hunter_db) -> None:
    """A 1.7-second unrelated write must not reject a reviewed manual job."""
    from app.database import get_connection

    raw = """Job Title: HR Operations Analyst
Company Name: Sample Employer
Location: Philadelphia, PA
Application: https://example.test/jobs/writer-contention
Job Description: Support HR reporting and recruiting coordination.
"""
    writer = get_connection()
    writer.execute("BEGIN IMMEDIATE")
    result: dict[str, object] = {}

    def save_in_background() -> None:
        try:
            result["saved"] = persist_manual_job(raw)
        except sqlite3.Error as error:
            result["error"] = error

    thread = threading.Thread(target=save_in_background, daemon=True)
    try:
        thread.start()
        time.sleep(1.7)  # Exceeds the former four 250-ms lock attempts.
        assert thread.is_alive(), result
    finally:
        writer.rollback()
        writer.close()
        thread.join(timeout=9)
    assert not thread.is_alive()
    assert "error" not in result, result
    saved = result["saved"]
    assert isinstance(saved, dict)
    assert saved["success"] is True
    assert saved["inserted"] is True
    again = persist_manual_job(raw)
    assert again["inserted"] is False
    assert again["job_id"] == saved["job_id"]
