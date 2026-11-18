"""Read-only, dynamic career overview contract tests."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from app import career_progress_snapshot_v1 as progress
from app.product_studio import career_progress_markup


@pytest.fixture
def database(monkeypatch, tmp_path):
    path = tmp_path / "progress.sqlite"

    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    monkeypatch.setattr(progress, "get_connection", connect)
    monkeypatch.setattr(
        progress, "current_owner_readonly",
        lambda _conn: SimpleNamespace(tenant_id="owner-t", user_id="owner-u"),
    )
    return connect


def test_missing_mirror_is_not_misrepresented_as_zero(database):
    assert progress.career_progress_snapshot() == progress.CareerProgress(None, None)


def test_deduplicated_live_snapshot_and_owner_scope(database):
    with database() as db:
        db.execute("""CREATE TABLE external_application_pipeline_v1 (
            tenant_id TEXT, user_id TEXT, provider TEXT,
            external_key TEXT, provider_application_sha256 TEXT,
            company_name TEXT, pipeline_stage TEXT)""")
        db.execute("""CREATE TABLE application_provider_routes_v1 (
            tenant_id TEXT, user_id TEXT, provider TEXT,
            provider_application_id_sha256 TEXT, state TEXT)""")
        rows = [
            ("owner-t", "owner-u", "TSENTA", "a", "h1", "Securitas", "INTERVIEWING"),
            ("owner-t", "owner-u", "TSENTA", "b", "h2",
             "Securitas Security Services USA, Inc.", "INTERVIEWING"),
            ("owner-t", "owner-u", "TSENTA", "c", "h3", "SAP", "INTERVIEWING"),
            ("owner-t", "owner-u", "TSENTA", "d", "h4", "Tyson", "REJECTED"),
            ("owner-t", "owner-u", "TSENTA", "e", "h5", "Another", "APPLIED"),
            ("owner-t", "owner-u", "TSENTA", "q", "h6", "Queued", "QUEUED"),
            ("owner-t", "another", "TSENTA", "x", "h7", "Foreign", "INTERVIEWING"),
        ]
        db.executemany(
            "INSERT INTO external_application_pipeline_v1 VALUES (?,?,?,?,?,?,?)", rows
        )
        db.executemany(
            "INSERT INTO application_provider_routes_v1 VALUES (?,?,?,?,?)",
            [
                ("owner-t", "owner-u", "TSENTA", "h1", "EMPLOYER_CONFIRMED"),
                ("owner-t", "owner-u", "TSENTA", "h9", "PROVIDER_SUBMITTED"),
                ("owner-t", "owner-u", "TSENTA", None, "PROVIDER_SUBMITTED"),
                ("owner-t", "owner-u", "TSENTA", "h10", "PREPARATION"),
                ("other", "owner-u", "TSENTA", "h11", "PROVIDER_SUBMITTED"),
            ],
        )
    assert progress.career_progress_snapshot() == progress.CareerProgress(6, 2)
    # No cached lifetime totals: visible count changes with persisted new status.
    with database() as db:
        db.execute(
            "UPDATE external_application_pipeline_v1 SET pipeline_stage=? "
            "WHERE external_key=?", ("INTERVIEWING", "e"),
        )
        db.commit()
    assert progress.career_progress_snapshot() == progress.CareerProgress(6, 3)


def test_actual_number_rendering_and_no_provider_naming():
    html = career_progress_markup(4851, 30)
    assert "4,851" in html and ">30<" in html
    assert "4.9K" not in html and "4.7K" not in html
    assert "Your progress" in html
    assert "tsenta" not in html.lower()
    assert "relay" not in html.lower()
    assert "Auto Apply" not in html
    assert "Temporarily unavailable" in career_progress_markup(None, None)


def test_fragment_refreshes_without_whole_page_rerun():
    import inspect

    from app import product_pages
    source = inspect.getsource(product_pages._dashboard_career_progress_fragment)
    assert "career_progress_snapshot()" in source
    # The fragment declaration lives next to the function and updates automatically.
    module = inspect.getsource(product_pages)
    assert '@st.fragment(run_every="60s")' in module
