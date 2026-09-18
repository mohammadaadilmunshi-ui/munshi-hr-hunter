from __future__ import annotations

from pathlib import Path

from app.resume_studio_page_v4 import _filter_resume_jobs


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
