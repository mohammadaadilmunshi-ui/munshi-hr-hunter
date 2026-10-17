from __future__ import annotations

import json

import pytest

from app import tsenta_learning_cohort_v1 as cohort
from app import tsenta_learning_sync_v1 as sync


class ExistingApplicationsMcp:
    def __init__(self, count: int = 12, *, malformed: bool = False):
        self.calls = []
        self.count = count
        self.malformed = malformed

    def call_read(self, name, params):
        self.calls.append((name, dict(params)))
        if name == "list_applications":
            if self.malformed:
                return {"applications": [{"applicationId": f"too-many-{i}"}
                                          for i in range(16)]}
            return {"applications": [
                {"applicationId": f"known-app-{i}", "atsType": (
                    "new-ats-dynamic", "oracle", "ashby", "workday"
                )[i % 4]}
                for i in range(self.count)
            ]}
        index = int(params["applicationId"].rsplit("-", 1)[1])
        if name == "get_application_status":
            return {
                "status": "COMPLETED" if index % 3 else "FAILED",
                "isTerminal": True,
                "needsAction": False,
                "atsType": ("new-ats-dynamic", "oracle", "ashby", "workday")[index % 4],
            }
        if name == "get_application_review":
            return {"awaitingReview": True, "fields": [
                {"label": "Country of Residence", "kind": "select",
                 "required": True, "value": "sensitive-candidate-answer",
                 "options": [
                     {"label": "United States", "value": "private-wire-choice"}
                 ]},
            ]}
        raise AssertionError("No execution tools may be invoked")


def test_authorized_import_reuses_read_only_mcp_across_multiple_ats(hunter_db):
    del hunter_db
    mcp = ExistingApplicationsMcp(count=12)
    report = sync.sync_observations_from_mcp(mcp=mcp, page=1, limit=15)
    assert report["distinct_applications_checked"] == 12
    assert report["status_observations"] == 12
    assert report["reviews_with_fields"] == 12
    assert report["candidate_answers_stored"] is False
    assert report["employer_receipts_verified"] == 0
    assert report["new_observation_rows"] == 24
    assert {name for name, _ in mcp.calls} == {
        "list_applications", "get_application_status", "get_application_review"
    }
    snapshot = cohort.learning_cohort_snapshot()
    assert snapshot["phase"] == "EVALUATION_READY"
    assert snapshot["processed_applications_total"] == 12
    assert snapshot["unique_ats_in_cohort"] == 4
    assert snapshot["ats_coverage"]["new-ats-dynamic"] == 3
    assert snapshot["auto_upgrade_enabled"] is False
    assert report["new_observation_rows"] + report["replayed_observation_rows"] == 24
    assert sync.sync_observations_from_mcp(mcp=mcp)["new_observation_rows"] == 0

    from app.database import get_connection
    with get_connection() as conn:
        stored = conn.execute(
            "SELECT observed_json FROM tsenta_observations_v1"
        ).fetchall()
    blob = json.dumps([r[0] for r in stored])
    for private in ("known-app-", "sensitive-candidate-answer", "private-wire-choice"):
        assert private not in blob


def test_learning_batch_rejects_out_of_bound_page_and_excess_records(hunter_db):
    del hunter_db
    fake = ExistingApplicationsMcp(count=3)
    for page, limit in ((0, 15), (21, 15), (1, 16), (1, 0)):
        with pytest.raises(ValueError):
            sync.sync_observations_from_mcp(mcp=fake, page=page, limit=limit)
    assert fake.calls == []
    with pytest.raises(RuntimeError):
        sync.sync_observations_from_mcp(
            mcp=ExistingApplicationsMcp(malformed=True), page=1, limit=15
        )


def test_provider_errors_do_not_create_successful_learning(hunter_db):
    del hunter_db

    class Malformed(ExistingApplicationsMcp):
        def call_read(self, name, params):
            if name == "get_application_status":
                raise RuntimeError("private MUNSHI Relay provider error")
            return super().call_read(name, params)

    report = sync.sync_observations_from_mcp(mcp=Malformed(count=3))
    assert report["status_observations"] == 0
    assert report["skipped"] == 3
    assert cohort.learning_cohort_snapshot()["processed_applications_total"] == 0
    assert "private MUNSHI Relay" not in str(report)


def test_sync_api_is_api_key_protected_and_never_mutates_applications(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET", "synthetic-only-test-secret")
    from fastapi.routing import APIRoute

    from app import api

    target = next(route for route in api.app.routes
                  if isinstance(route, APIRoute)
                  and route.path == "/api/integrations/tsenta/learning/sync")
    assert {dep.call.__name__ for dep in target.dependant.dependencies} == {
        "require_api_secret"
    }

    monkeypatch.setattr(
        "app.tsenta_learning_sync_v1.sync_authorized_learning",
        lambda **kwargs: {"page": kwargs["page"], "new_observation_rows": 0},
    )
    response = api.sync_tsenta_learning_observations(page=2, limit=10)
    assert response["page"] == 2
    assert response["new_observation_rows"] == 0
