from __future__ import annotations

import sqlite3

import pytest

from app import job_card_runtime_hotfix_v1 as hotfix


def _connection(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_job_bundle_tolerates_missing_and_partial_optional_history(tmp_path, monkeypatch) -> None:
    db = tmp_path / "job-card.db"
    connection = _connection(str(db))
    try:
        connection.executescript(
            """
            CREATE TABLE jobs(
                id INTEGER PRIMARY KEY,
                title TEXT,
                company_name TEXT
            );
            INSERT INTO jobs(id,title,company_name)
            VALUES (7,'People Analyst','Example Co');

            -- Deliberately partial legacy schemas: no artifact URL columns and
            -- no product_job_state table at all.
            CREATE TABLE n8n_results(
                id INTEGER PRIMARY KEY,
                job_id INTEGER,
                n8n_status TEXT
            );
            INSERT INTO n8n_results(id,job_id,n8n_status)
            VALUES (1,7,'completed');

            CREATE TABLE n8n_dispatch_queue(
                job_id INTEGER,
                queue_status TEXT
            );
            INSERT INTO n8n_dispatch_queue(job_id,queue_status)
            VALUES (7,'completed');
            """
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(hotfix, "get_connection", lambda: _connection(str(db)))

    bundle = hotfix._safe_job_bundle(7)
    assert bundle is not None
    assert bundle["title"] == "People Analyst"
    assert bundle["saved"] == 0
    assert bundle["skipped"] == 0
    assert bundle["latest_result"]["n8n_status"] == "completed"
    assert bundle["artifact_result"] == {}
    assert bundle["queue"]["queue_status"] == "completed"


def test_dialog_failure_is_logged_and_forces_clean_rerun(monkeypatch) -> None:
    class Pages:
        feedback = None

        @staticmethod
        def _set_action_feedback(tone, message):
            Pages.feedback = (tone, message)

    class ProductV22:
        @staticmethod
        def _opportunity_dialog(_job_id):
            raise RuntimeError("synthetic drawer failure")

    class Rerun(Exception):
        pass

    monkeypatch.setattr(hotfix.st, "rerun", lambda: (_ for _ in ()).throw(Rerun()))

    with pytest.raises(Rerun):
        hotfix._safe_open_dialog(ProductV22, Pages, 42)

    assert Pages.feedback is not None
    assert Pages.feedback[0] == "error"
    assert "could not open safely" in Pages.feedback[1]


def test_installer_keeps_job_browsing_non_submitting() -> None:
    from app import product_pages, product_v22

    original_bundle = product_v22._job_bundle
    original_detail = product_pages._job_detail
    try:
        state = hotfix.install_job_card_runtime_hotfix()
        assert state["success"] is True
        assert state["optional_n8n_history_required_for_browsing"] is False
        assert state["submission_authority"] is False
        assert product_v22._job_bundle is hotfix._safe_job_bundle
        assert getattr(product_pages._job_detail, "_aadil_job_card_fail_soft_v1", False) is True
    finally:
        product_v22._job_bundle = original_bundle
        product_pages._job_detail = original_detail
