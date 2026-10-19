from __future__ import annotations

import pytest

from app import tsenta_learning_monitor_v1 as monitor


def _connected():
    return {"authorized": True, "profile_read_verified": True,
            "execution_ready": False}


def test_disabled_monitor_never_contacts_provider_or_changes_database(monkeypatch):
    monkeypatch.delenv(monitor.ENABLED_ENV, raising=False)
    monkeypatch.setattr(monitor.oauth, "status",
                        lambda: (_ for _ in ()).throw(AssertionError("must not connect")))
    result = monitor.learning_cycle()
    assert result["status"] == "DISABLED"
    assert result["provider_submissions_started"] == 0
    assert result["new_observations"] == 0


def test_unconnected_monitor_does_not_sync_even_if_enabled(monkeypatch):
    monkeypatch.setenv(monitor.ENABLED_ENV, "true")
    monkeypatch.setattr(monitor.oauth, "status",
                        lambda: {"authorized": False, "profile_read_verified": False})
    monkeypatch.setattr(monitor.sync, "sync_authorized_learning",
                        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("not allowed")))
    report = monitor.learning_cycle()
    assert report["status"] == "BACKEND_TSENTA_AUTH_REQUIRED"
    assert report["enabled"] is False


def test_authorized_monitor_paginates_only_existing_mcp_evidence(monkeypatch):
    monkeypatch.setenv(monitor.ENABLED_ENV, "true")
    monkeypatch.setattr(monitor.oauth, "status", _connected)
    calls = []
    def fake_sync(**kwargs):
        calls.append(kwargs)
        count = 15 if kwargs["page"] == 1 else 4
        return {
            "listed_applications": count,
            "distinct_applications_checked": count,
            "new_observation_rows": count * 2,
            "replayed_observation_rows": 0,
        }
    monkeypatch.setattr(monitor.sync, "sync_authorized_learning", fake_sync)
    monkeypatch.setattr(monitor.cohort, "learning_cohort_snapshot",
                        lambda: {"phase": "EVALUATION_READY",
                                 "processed_applications_total": 12,
                                 "cohort_processed": 12,
                                 "unique_ats_in_cohort": 4,
                                 "shadow_improvement_candidates": [{"deployment_state": "SHADOW_ONLY"}]})
    outcome=monitor.learning_cycle(pages=6)
    assert calls==[{"page":1,"limit":15},{"page":2,"limit":15}]
    assert outcome["provider_applications_seen"] == 19
    assert outcome["pages_checked"] == 2
    assert outcome["status"] == "COMPLETED_READ_ONLY"
    assert outcome["shadow_candidates"] == 1
    assert outcome["provider_submissions_started"] == 0
    assert outcome["native_auto_upgrades_applied"] == 0


@pytest.mark.parametrize("pages,limit", [(0,15),(21,15),(1,0),(1,16)])
def test_monitor_enforces_bounded_pages(pages, limit):
    with pytest.raises(ValueError):
        monitor.learning_cycle(pages=pages,limit=limit)


def test_monitor_error_does_not_expose_provider_error_text(monkeypatch,capsys):
    monkeypatch.setenv(monitor.ENABLED_ENV,"true")
    monkeypatch.setattr(monitor.oauth,"status",_connected)
    monkeypatch.setattr(monitor.sync,"sync_authorized_learning",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("sensitive@example.com SECRET_ACCOUNT_VALUE")))
    monkeypatch.setattr("sys.argv",["learning_monitor","--once"])
    assert monitor.main()==1
    out=capsys.readouterr().out
    assert "SAFE_ABORT" in out
    assert "sensitive@example.com" not in out
    assert "SECRET_ACCOUNT_VALUE" not in out



def test_complete_history_window_marks_last_short_page(monkeypatch):
    monkeypatch.setenv(monitor.ENABLED_ENV, "true")
    monkeypatch.setattr(monitor.oauth, "status", _connected)
    monkeypatch.setattr(
        monitor.sync, "sync_authorized_learning",
        lambda page, limit: {
            "listed_applications": 15 if page == 1 else 2,
            "distinct_applications_checked": 15 if page == 1 else 2,
            "new_observation_rows": 0, "replayed_observation_rows": 0,
        },
    )
    monkeypatch.setattr(
        monitor.cohort, "learning_cohort_snapshot",
        lambda: {"phase": "COLLECTING", "processed_applications_total": 1,
                 "cohort_processed": 1, "unique_ats_in_cohort": 1,
                 "shadow_improvement_candidates": []},
    )
    result = monitor.learning_cycle(pages=20)
    assert result["pages_checked"] == 2
    assert result["history_window_complete"] is True
    assert result["history_page_cap_reached"] is False


def test_full_history_cap_never_claims_all_records_learned(monkeypatch):
    monkeypatch.setenv(monitor.ENABLED_ENV, "true")
    monkeypatch.setattr(monitor.oauth, "status", _connected)
    pages = []
    def page_of_evidence(*, page, limit):
        pages.append(page)
        return {"listed_applications": limit, "distinct_applications_checked": limit,
                "new_observation_rows": 0, "replayed_observation_rows": limit}
    monkeypatch.setattr(monitor.sync, "sync_authorized_learning", page_of_evidence)
    monkeypatch.setattr(
        monitor.cohort, "learning_cohort_snapshot",
        lambda: {"phase": "COLLECTING", "processed_applications_total": 1,
                 "cohort_processed": 1, "unique_ats_in_cohort": 1,
                 "shadow_improvement_candidates": []},
    )
    result = monitor.learning_cycle(pages=20)
    assert pages == list(range(1, 21))
    assert result["history_window_complete"] is False
    assert result["history_page_cap_reached"] is True
    assert result["provider_submissions_started"] == 0
