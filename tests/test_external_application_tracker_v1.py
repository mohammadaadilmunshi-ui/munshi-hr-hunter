from __future__ import annotations

import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from app import external_application_tracker_v1 as tracker


def _record(i: int, stage: str = "APPLIED", **extra):
    return {
        "id": f"track-{i}",
        "applicationId": f"submitted-{i}",
        "companyName": f"Employer {i}",
        "positionTitle": "People Analyst",
        "status": stage,
        "dateApplied": "2026-10-20",
        "statusHistory": [
            {"status": "APPLIED", "changedAt": "2026-10-20T10:00:00Z"},
            {"status": stage, "changedAt": "2026-10-21T11:00:00Z"},
        ],
        "notes": "Private notes must never be stored",
        "latestEmailPreview": "Private recruiter email",
        **extra,
    }


@pytest.fixture
def isolated_database(monkeypatch, tmp_path):
    path = tmp_path / "tracker.sqlite"

    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db

    owner = SimpleNamespace(tenant_id="tenant-one", user_id="user-one")
    monkeypatch.setattr(tracker, "get_connection", connect)
    monkeypatch.setattr(tracker, "ensure_tenant_schema", lambda _conn: None)
    monkeypatch.setattr(tracker, "current_owner", lambda _conn: owner)
    monkeypatch.setattr(tracker, "current_owner_readonly", lambda _conn: owner)
    return connect, owner


def test_normalization_keeps_only_safe_pipeline_fields():
    record = _record(1, "INTERVIEWING")
    normalized = tracker.normalize_tracked_application(record)
    assert normalized["pipeline_stage"] == "INTERVIEWING"
    assert normalized["provider_application_sha256"] == hashlib.sha256(
        b"submitted-1"
    ).hexdigest()
    serialized = str(normalized)
    assert "Private notes" not in serialized
    assert "Private recruiter email" not in serialized
    assert "track-1" not in serialized
    assert "submitted-1" not in serialized
    assert "2026-10-21" in serialized


def test_unrecognized_status_never_becomes_submitted():
    assert tracker.normalize_tracked_application(_record(1, "UNKNOWN")) is None
    assert tracker.normalize_tracked_application(_record(1, "COMPLETED")) is None
    assert tracker.normalize_tracked_application(_record(1, "OFFER"))["pipeline_stage"] == "OFFER"


def test_upserts_are_idempotent_and_owner_scoped(isolated_database):
    connect, owner = isolated_database
    first = tracker.persist_tracked_rows([_record(1)])
    second = tracker.persist_tracked_rows([_record(1, "INTERVIEWING")])
    assert first == {"inserted": 1, "updated": 0, "skipped": 0}
    assert second == {"inserted": 0, "updated": 1, "skipped": 0}
    views = tracker.external_application_views()
    assert len(views) == 1
    assert views[0].canonical_state == "INTERVIEWING"
    assert views[0].job_id is None
    assert len(views[0].status_history) == 2
    owner.user_id = "user-two"
    assert tracker.external_application_views() == []
    owner.user_id = "user-one"
    with connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM external_application_pipeline_v1"
        ).fetchone()[0] == 1
        columns = [col[1] for col in connection.execute(
            "PRAGMA table_info(external_application_pipeline_v1)"
        )]
        assert "notes" not in columns
        assert "email" not in " ".join(columns)
        assert "application_url" not in columns


def test_strong_identity_prevents_double_card(isolated_database):
    connect, _ = isolated_database
    tracker.persist_tracked_rows([_record(1), _record(2)])
    with connect() as connection:
        connection.execute(
            """CREATE TABLE application_provider_routes_v1 (
                tenant_id TEXT, user_id TEXT, provider TEXT,
                provider_application_id_sha256 TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO application_provider_routes_v1 VALUES(?,?,?,?)",
            (
                "tenant-one", "user-one", "TSENTA",
                hashlib.sha256(b"submitted-1").hexdigest(),
            ),
        )
    visible = tracker.external_application_views()
    assert len(visible) == 1
    assert visible[0].company == "Employer 2"


def test_similar_employers_do_not_hide_distinct_applications(isolated_database):
    rows = [
        _record(1),
        _record(2, companyName="Employer 1", positionTitle="People Analyst"),
    ]
    tracker.persist_tracked_rows(rows)
    assert len(tracker.external_application_views()) == 2


class FakeReadClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def call_read(self, name, params):
        self.calls.append((name, dict(params)))
        return {"structuredContent": {"result": {
            "applications": self.pages[params["page"] - 1]
        }}}


def test_bounded_read_only_pagination(isolated_database):
    client = FakeReadClient([
        [_record(index) for index in range(10)],
        [_record(10, "OFFER"), _record(11, "INTERVIEWING")],
    ])
    result = tracker.sync_tracker_from_mcp(mcp=client, max_pages=3)
    assert result["checked"] == 12
    assert result["inserted"] == 12
    assert result["employer_receipts_claimed"] == 0
    assert result["provider_reads_only"] is True
    assert [name for name, _ in client.calls] == [
        "list_tracked_applications", "list_tracked_applications"
    ]
    assert len(tracker.external_application_views()) == 12


def test_full_history_sync_fetches_all_804_rows_and_reports_progress(isolated_database):
    records = [
        _record(i, "INTERVIEWING" if i % 7 == 0 else "APPLIED")
        for i in range(804)
    ]

    class FullHistoryProvider:
        def __init__(self):
            self.calls = []

        def call_read(self, name, params):
            assert name == "list_tracked_applications"
            self.calls.append(dict(params))
            start = (params["page"] - 1) * params["limit"]
            return {"structuredContent": {"result": {
                "applications": records[start:start + params["limit"]],
                "pagination": {
                    "total": len(records),
                    "hasMore": start + params["limit"] < len(records),
                },
            }}}

    provider = FullHistoryProvider()
    progress = []
    result = tracker.sync_tracker_from_mcp(
        mcp=provider,
        max_pages=tracker.MAX_PAGES,
        on_progress=lambda checked, total: progress.append((checked, total)),
    )
    assert result["checked"] == 804
    assert result["inserted"] == 804
    assert result["provider_total"] == 804
    assert result["complete"] is True
    assert result["employer_receipts_claimed"] == 0
    assert result["provider_reads_only"] is True
    assert len(provider.calls) == 81
    assert all(call["limit"] == 10 for call in provider.calls)
    assert all(call["startDate"] == "2020-01-01" for call in provider.calls)
    assert progress[-1] == (804, 804)
    assert len(tracker.external_application_views(limit=5000)) == 804


def test_bounded_sync_never_claims_full_coverage_when_more_pages_remain(
    isolated_database,
):
    class ManyPages:
        def call_read(self, name, params):
            page = params["page"]
            return {"structuredContent": {"result": {
                "applications": [_record(i + page * 10) for i in range(10)],
                "pagination": {"total": 804, "hasMore": True},
            }}}

    report = tracker.sync_tracker_from_mcp(mcp=ManyPages(), max_pages=2)
    assert report["checked"] == 20
    assert report["provider_total"] == 804
    assert report["complete"] is False


def test_provider_result_shapes_stay_bounded():
    record = _record(3)
    assert tracker._rows({"data": [record]}) == [record]
    assert tracker._rows({"data": {"results": [record]}}) == [record]
    with pytest.raises(ValueError, match="bounded"):
        tracker._rows({"data": [record] * 11})


def test_repeated_pages_stop_safely(isolated_database):
    page = [_record(index) for index in range(10)]
    client = FakeReadClient([page, page])
    result = tracker.sync_tracker_from_mcp(mcp=client, max_pages=2)
    assert result["checked"] == 10
    assert len(client.calls) == 2


def test_invalid_shape_does_not_write(isolated_database):
    class Malformed:
        def call_read(self, name, params):
            return {"structuredContent": {"result": {"unexpected": []}}}

    with pytest.raises(TypeError, match="shape"):
        tracker.sync_tracker_from_mcp(mcp=Malformed())
    assert tracker.external_application_views() == []


def test_page_boundaries():
    class NeverCalled:
        def call_read(self, *args, **kwargs):
            raise AssertionError("External call must not occur")

    with pytest.raises(ValueError):
        tracker.sync_tracker_from_mcp(mcp=NeverCalled(), max_pages=0)
    with pytest.raises(ValueError):
        tracker.sync_tracker_from_mcp(mcp=NeverCalled(), max_pages=tracker.MAX_PAGES + 1)


def test_native_route_shows_tracker_stage_without_new_application(isolated_database):
    connect, _ = isolated_database
    tracker.persist_tracked_rows([_record(4, "INTERVIEWING")])
    with connect() as connection:
        connection.execute(
            """CREATE TABLE application_provider_routes_v1 (
                tenant_id TEXT, user_id TEXT, provider TEXT, job_id INTEGER,
                provider_application_id_sha256 TEXT
            )"""
        )
        connection.execute(
            "INSERT INTO application_provider_routes_v1 VALUES(?,?,?,?,?)",
            ("tenant-one", "user-one", "TSENTA", 44,
             hashlib.sha256(b"submitted-4").hexdigest()),
        )
    assert tracker.external_application_views() == []
    linked = tracker.linked_application_stage_for_job(44)
    assert linked["stage"] == "INTERVIEWING"
    assert linked["employer_receipt_verified"] is False
    assert linked["history"][0]["status"] == "APPLIED"
    assert tracker.linked_application_stage_for_job(45) is None
