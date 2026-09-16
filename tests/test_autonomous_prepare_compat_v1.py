from __future__ import annotations

from app import autonomous_prepare_admission_v1 as admission
from app import autonomous_prepare_compat_v1 as compat


class FakeConnection:
    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _job(job_id: int):
    return {
        "id": job_id,
        "job_fingerprint": f"job-{job_id}",
        "company_name": "Acme",
        "title": f"People Analyst {job_id}",
        "hunter_score": 99.0,
    }


def _plan(*, manual=None, auto_limit=4, auto_used=1):
    return {
        "threshold": 95.0,
        "auto_limit": auto_limit,
        "product_volume_mode": "custom_limit",
        "enabled_lane_count": 1,
        "manual_limit": 25,
        "auto_used_today": auto_used,
        "manual_used_today": 0,
        "manual_candidates": list(manual or []),
        "auto_candidates": [],
    }


def test_missing_prepare_engine_preserves_historical_n8n_default(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "unlimited",
            "daily_limit": None,
            "review_first": True,
        },
    )

    assert compat.configured_engine() == "N8N"


def test_explicit_native_prepare_engine_is_honored(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "unlimited",
            "prepare_engine": "NATIVE",
        },
    )

    assert compat.configured_engine() == "NATIVE"


def test_explicit_n8n_prepare_engine_is_honored(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {
            "mode": "custom_limit",
            "prepare_engine": "N8N",
        },
    )

    assert compat.configured_engine() == "N8N"


def test_invalid_saved_engine_fails_closed(monkeypatch):
    monkeypatch.setattr(compat, "get_connection", lambda: FakeConnection())
    monkeypatch.setattr(
        compat,
        "load_setting",
        lambda connection, key, fallback: {"prepare_engine": "unexpected"},
    )

    try:
        compat.configured_engine()
    except RuntimeError as error:
        assert "invalid prepare engine" in str(error)
    else:
        raise AssertionError("invalid prepare engine must fail closed")


def test_n8n_compat_excludes_durable_jobs_and_shares_auto_quota(monkeypatch):
    connection = FakeConnection()
    manual_existing = _job(1)
    manual_new = _job(2)
    auto_pool = [_job(11), _job(12), _job(13)]

    monkeypatch.setattr(compat, "get_connection", lambda: connection)
    monkeypatch.setattr(compat, "ensure_n8n_schema", lambda _connection: None)
    monkeypatch.setattr(
        compat,
        "plan_candidates",
        lambda _connection: _plan(
            manual=[manual_existing, manual_new],
            auto_limit=4,
            auto_used=1,
        ),
    )
    monkeypatch.setattr(
        compat.runtime,
        "_existing_prepare_job_ids",
        lambda _connection: {1, 10},
    )
    monkeypatch.setattr(
        compat.runtime,
        "_admissions_today",
        lambda _connection: 1,
    )
    monkeypatch.setattr(
        admission,
        "_eligible_unprepared_pool",
        lambda _connection, threshold: auto_pool,
    )

    result = compat.queue_legacy_n8n_candidates(
        webhook_mode="production",
        dry_run=True,
    )

    # Four daily auto slots minus one legacy n8n use and one durable admission
    # leaves two auto slots. The manually approved job already owned by durable
    # Prepare is also excluded, so no job is prepared twice after an engine switch.
    assert result["remaining_slots"] == 2
    assert result["durable_auto_used_today"] == 1
    assert [row["job_id"] for row in result["queued"]] == [2, 11, 12]
    assert [row["dispatch_mode"] for row in result["queued"]] == [
        "telegram_manual",
        "auto_top_match",
        "auto_top_match",
    ]
    assert result["submission_authority"] is False
