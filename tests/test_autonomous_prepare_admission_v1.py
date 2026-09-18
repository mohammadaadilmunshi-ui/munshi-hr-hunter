from __future__ import annotations

from app import autonomous_prepare_admission_v1 as admission


class FakeConnection:
    def close(self):
        return None


def _job(job_id: int, score: float = 99.0):
    return {
        "id": job_id,
        "job_fingerprint": f"job-{job_id}",
        "company_name": "Acme",
        "title": f"People Analyst {job_id}",
        "hunter_score": score,
    }


def _plan(*, auto_limit=2, auto_used=0):
    return {
        "threshold": 95.0,
        "auto_limit": auto_limit,
        "product_volume_mode": "custom_limit",
        "enabled_lane_count": 0,
        "manual_limit": 25,
        "auto_used_today": auto_used,
        "manual_used_today": 0,
        "manual_candidates": [],
        # Intentionally represents the legacy planner's already-truncated list.
        # The durable admission implementation must not consume this field.
        "auto_candidates": [_job(1), _job(2)],
    }


def test_existing_prepare_rows_are_filtered_before_quota_slice(monkeypatch):
    jobs = [_job(index) for index in range(1, 8)]
    monkeypatch.setattr(admission, "load_unqueued_jobs", lambda connection: jobs)
    monkeypatch.setattr(
        admission.runtime,
        "_existing_prepare_job_ids",
        lambda connection: {1, 2, 3, 4, 5},
    )
    monkeypatch.setattr(admission, "enabled_lanes", lambda connection: [])
    monkeypatch.setattr(admission, "load_rules", lambda: {"fixture": True})
    monkeypatch.setattr(
        admission,
        "is_auto_eligible",
        lambda job, threshold, targeting_rules: float(job["hunter_score"]) >= threshold,
    )

    pool = admission._eligible_unprepared_pool(
        FakeConnection(),
        threshold=95.0,
    )
    assert [row["id"] for row in pool] == [6, 7]


def test_finite_quota_selects_new_jobs_not_legacy_truncated_window(monkeypatch):
    connection = FakeConnection()
    starts = []

    monkeypatch.setattr(admission, "get_connection", lambda: connection)
    monkeypatch.setattr(admission, "ensure_n8n_schema", lambda connection: None)
    monkeypatch.setattr(admission.orchestrator, "ensure_schema", lambda: None)
    monkeypatch.setattr(admission, "plan_candidates", lambda connection: _plan(auto_limit=2))
    monkeypatch.setattr(admission.runtime, "_configured_engine", lambda connection: "NATIVE")
    monkeypatch.setattr(admission.runtime, "_admissions_today", lambda connection: 0)
    monkeypatch.setattr(
        admission,
        "_eligible_unprepared_pool",
        lambda connection, threshold: [_job(6), _job(7), _job(8)],
    )

    def start(job_id, *, engine):
        starts.append((job_id, engine))
        return {"state": "QUEUED", "replayed": False}

    monkeypatch.setattr(admission.product_flow, "start_prepare_for_job", start)
    monkeypatch.setattr(admission.runtime, "_record_event", lambda *args, **kwargs: None)

    result = admission.admit_auto_candidates(dry_run=False)
    assert starts == [(6, "NATIVE"), (7, "NATIVE")]
    assert [row["job_id"] for row in result["admitted"]] == [6, 7]
    assert result["remaining_slots"] == 2
    assert result["eligible_unprepared_count"] == 3


def test_combined_legacy_and_durable_usage_reduces_remaining_slots(monkeypatch):
    connection = FakeConnection()
    starts = []

    monkeypatch.setattr(admission, "get_connection", lambda: connection)
    monkeypatch.setattr(admission, "ensure_n8n_schema", lambda connection: None)
    monkeypatch.setattr(admission.orchestrator, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        admission,
        "plan_candidates",
        lambda connection: _plan(auto_limit=5, auto_used=1),
    )
    monkeypatch.setattr(admission.runtime, "_configured_engine", lambda connection: "NATIVE")
    monkeypatch.setattr(admission.runtime, "_admissions_today", lambda connection: 2)
    monkeypatch.setattr(
        admission,
        "_eligible_unprepared_pool",
        lambda connection, threshold: [_job(10), _job(11), _job(12)],
    )

    def start(job_id, *, engine):
        starts.append(job_id)
        return {"state": "QUEUED", "replayed": False}

    monkeypatch.setattr(admission.product_flow, "start_prepare_for_job", start)
    monkeypatch.setattr(admission.runtime, "_record_event", lambda *args, **kwargs: None)

    result = admission.admit_auto_candidates(dry_run=False)
    assert result["remaining_slots"] == 2
    assert starts == [10, 11]


def test_unlimited_mode_keeps_all_eligible_unprepared_jobs(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(admission, "get_connection", lambda: connection)
    monkeypatch.setattr(admission, "ensure_n8n_schema", lambda connection: None)
    monkeypatch.setattr(admission.orchestrator, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        admission,
        "plan_candidates",
        lambda connection: _plan(auto_limit=None, auto_used=0),
    )
    monkeypatch.setattr(admission.runtime, "_configured_engine", lambda connection: "NATIVE")
    monkeypatch.setattr(admission.runtime, "_admissions_today", lambda connection: 0)
    monkeypatch.setattr(
        admission,
        "_eligible_unprepared_pool",
        lambda connection, threshold: [_job(20), _job(21), _job(22)],
    )
    monkeypatch.setattr(
        admission.product_flow,
        "start_prepare_for_job",
        lambda job_id, engine: {"state": "QUEUED", "replayed": False},
    )
    monkeypatch.setattr(admission.runtime, "_record_event", lambda *args, **kwargs: None)

    result = admission.admit_auto_candidates(dry_run=False)
    assert result["remaining_slots"] is None
    assert [row["job_id"] for row in result["admitted"]] == [20, 21, 22]
    assert result["submission_authority"] is False
