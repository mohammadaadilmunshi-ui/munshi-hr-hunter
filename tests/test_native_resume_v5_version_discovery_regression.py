"""Regression for Resume Studio losing validated V5 versions after generation."""
from __future__ import annotations

from app import native_resume_service_v5 as v5


def test_v5_list_versions_keeps_its_stage_b_enrichment(monkeypatch):
    """A V1 alias at EOF previously overwrote this wrapper in production."""
    assert v5.list_versions.__module__ == "app.native_resume_service_v5"
    monkeypatch.setattr(
        v5.v4, "list_versions",
        lambda **kwargs: [{"version_id": "version-verified", "job_id": 54, "version_number": 3}],
    )
    monkeypatch.setattr(
        v5.stage_b_binding,
        "resume_stage_b_binding",
        lambda version_id: {
            "plan_id": "plan-1", "plan_digest": "digest-1",
            "trace_id": "trace-1",
        },
    )
    records = v5.list_versions(job_id=54, limit=5)
    assert records[0]["stage_b_bound"] is True
    assert records[0]["stage_b_plan_id"] == "plan-1"


def test_v5_list_versions_does_not_invent_missing_binding(monkeypatch):
    monkeypatch.setattr(
        v5.v4, "list_versions",
        lambda **kwargs: [{"version_id": "unbound", "job_id": 54}],
    )
    monkeypatch.setattr(
        v5.stage_b_binding, "resume_stage_b_binding",
        lambda version_id: None,
    )
    assert v5.list_versions(job_id=54)[0]["stage_b_bound"] is False


def test_studio_reuses_existing_v5_resume_without_generation(monkeypatch):
    from app import resume_studio_page_v4 as studio
    from app.phase67_common import safe_owned_job_snapshot
    # The summary, unlike the full record, is intentionally sparse. V5
    # must enrich it before the Studio duplicate-prevention decision.
    monkeypatch.setattr(
        v5.v4, "list_versions",
        lambda **kwargs: [{"version_id": "verified-3", "job_id": 54, "version_number": 3}],
    )
    monkeypatch.setattr(
        v5.stage_b_binding, "resume_stage_b_binding",
        lambda _: {"plan_id": "plan-3", "plan_digest": "digest-3", "trace_id": "trace-3"},
    )
    monkeypatch.setattr(
        studio.truth_binding, "current_candidate_profile_snapshot",
        lambda: {"profile_digest": "truth-3"},
    )
    import app.phase67_common as phase67
    monkeypatch.setattr(
        phase67, "safe_owned_job_snapshot",
        lambda job_id: {"job_snapshot_sha256": "job-3"},
    )
    monkeypatch.setattr(
        v5, "get_version",
        lambda _: {
            "version_id": "verified-3", "job_id": 54, "version_number": 3,
            "stage_b_bound": True,
            "candidate_truth_binding": {"profile_digest": "truth-3"},
            "job_snapshot_binding": {"job_snapshot_sha256": "job-3"},
        },
    )
    result = studio._reusable_exact_resume(54)
    assert result is not None
    assert result["version_id"] == "verified-3"
