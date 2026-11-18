"""New job, Aggressive V5, evidence-bound one-page PDF: no live submission.

Uses isolated test DB and deterministic fake model response. This is a
structural E2E test, not a claim of a genuine model's resume-writing quality.
"""
from app import native_resume_service_v5 as resume
from app import native_resume_artifact_v5 as artifact
from tests.test_stage_b_native_resume_v5 import _confirmed_profile, _fake_writer, _job
from tests.test_native_resume_artifact_v5 import _renderers


def test_fresh_aggressive_optimizes_supported_jd_without_submission(hunter_db, monkeypatch):
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()
    result = resume.generate_resume(job_id=job_id)
    assert result["stage_b_bound"] is True
    assert result["diagnostics"]["rewrite_mode"] == "aggressive"
    assert result["diagnostics"]["stage_b"]["claim_guard"]["status"] == "PASS"
    _renderers(monkeypatch)
    bundle=artifact.materialize(result["version_id"])
    assert bundle["pdf"]["page_count"] == 1
    assert bundle["page_verification"]["status"] == "PASS"
    assert result["candidate_truth_bound"] is True
    assert result["job_snapshot_bound"] is True
    assert bundle["submission_authority"] is False
    content = str(result["document"])
    assert "Excel" in content and "Power BI" in content
    assert "Configured Workday" not in content
    # A generated resume does not write an application receipt.
    from app.database import get_connection
    with get_connection() as con:
        exists=con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='application_provider_routes_v1'"
        ).fetchone()[0]
        if exists:
            assert con.execute(
                "SELECT count(*) FROM application_provider_routes_v1 WHERE job_id=?",
                (job_id,)
            ).fetchone()[0] == 0
