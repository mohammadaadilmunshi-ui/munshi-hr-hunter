from __future__ import annotations

from pathlib import Path

from app.resume_studio_page_v4 import _filter_resume_jobs, _filter_resume_library
from app import resume_studio_page_v4


def _rows() -> list[dict]:
    return [
        {
            "id": 1,
            "company_name": "Acme",
            "title": "People Analyst",
            "location_raw": "New York, NY",
            "source": "Greenhouse",
            "description_raw": "Power BI workforce analytics",
            "hunter_score": 91,
            "saved": True,
            "prepared": False,
            "first_seen_at": "2026-09-08T10:00:00+00:00",
        },
        {
            "id": 2,
            "company_name": "Beta",
            "title": "Recruiting Coordinator",
            "location_raw": "Austin, TX",
            "source": "Indeed",
            "description_raw": "Talent acquisition operations",
            "hunter_score": 63,
            "saved": False,
            "prepared": True,
            "first_seen_at": "2026-09-09T10:00:00+00:00",
        },
    ]


def test_resume_job_finder_searches_company_title_location_source_and_jd() -> None:
    rows = _rows()
    assert [row["id"] for row in _filter_resume_jobs(rows, query="power bi")] == [1]
    assert [row["id"] for row in _filter_resume_jobs(rows, query="austin indeed")] == [2]


def test_resume_job_finder_filters_saved_prepared_and_match() -> None:
    rows = _rows()
    assert [row["id"] for row in _filter_resume_jobs(rows, result_set="saved")] == [1]
    assert [row["id"] for row in _filter_resume_jobs(rows, result_set="prepared")] == [2]
    assert [row["id"] for row in _filter_resume_jobs(rows, minimum_match=80)] == [1]


def test_resume_job_finder_has_compact_search_ui_not_giant_job_selectbox() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "resume_studio_page_v4.py").read_text(
        encoding="utf-8"
    )
    selector = (root / "app" / "resume_engine_selector.py").read_text(
        encoding="utf-8"
    )
    assert "Change target job" in source
    assert "Search company, title, location, source, or JD keyword" in source
    assert '"saved", "prepared"' in source
    assert "native_resume_job_id" in source
    assert "last_viewed_job_id" in source
    assert "_resume_job_card_html" in source
    assert "Use this job" in source
    assert "expanded=False" in source
    assert "job ID" in source
    assert 'st.session_state["native_resume_job_id"] = int(job_id)' in selector
    assert 'st.selectbox(\n        "Job",' not in source


def test_tailored_resume_library_searches_and_filters_versions() -> None:
    rows = [
        {
            "version_id": "oklo-current",
            "company_name": "Oklo",
            "title": "People Operations Associate",
            "application_status": "Preparing",
            "created_at": "2026-09-22T12:00:00-04:00",
            "is_current": True,
            "application_id": "application-oklo",
        },
        {
            "version_id": "acme-old",
            "company_name": "Acme",
            "title": "HR Analyst",
            "application_status": "No application",
            "created_at": "2026-01-01T12:00:00-05:00",
            "is_current": False,
            "application_id": "",
        },
    ]
    assert [
        row["version_id"]
        for row in _filter_resume_library(
            rows, query="Oklo people", status="all", date_range="all"
        )
    ] == ["oklo-current"]
    assert [
        row["version_id"]
        for row in _filter_resume_library(
            rows, query="", status="linked", date_range="all"
        )
    ] == ["oklo-current"]


def test_resume_library_uses_canonical_nested_application_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        resume_studio_page_v4.resume_v5,
        "list_versions",
        lambda limit=200: [
            {
                "version_id": "resume-301",
                "version_number": 1,
                "job_id": 301,
                "stage_b_bound": True,
                "diagnostics": {},
            }
        ],
    )
    monkeypatch.setattr(
        resume_studio_page_v4,
        "_resume_job_catalog",
        lambda limit=500: [
            {"id": 301, "company_name": "Example", "title": "People Partner"}
        ],
    )
    from app import product_application_flow_v2

    monkeypatch.setattr(
        product_application_flow_v2,
        "flow_for_job",
        lambda _job_id, summary=True: {
            "display_status": "Needs review",
            "prepare": {"application_id": "application-301"},
            "lifecycle": None,
        },
    )

    rows = resume_studio_page_v4._resume_library_rows()

    assert rows[0]["application_id"] == "application-301"
    assert rows[0]["application_status"] == "Needs review"
    assert [
        row["version_id"]
        for row in _filter_resume_library(
            rows, query="", status="linked", date_range="all"
        )
    ] == ["resume-301"]
