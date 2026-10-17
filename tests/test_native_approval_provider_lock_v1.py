"""Native package consent and provider ownership share one durable lock."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import product_application_flow_v2 as flow


def test_native_route_binds_exact_verified_url_before_mandate(monkeypatch):
    from app import application_provider_router_v1 as router
    from app import application_provider_ui_v1 as ui

    class FakeDB:
        closed = False

        def execute(self, query, args=()):
            if query.startswith("PRAGMA"):
                return SimpleNamespace(fetchone=lambda: None)
            assert "SELECT * FROM jobs WHERE id=?" in query
            assert args == (42,)
            return SimpleNamespace(
                fetchone=lambda: {"id": 42, "company_name": "Example", "title": "HR Analyst"}
            )

        def close(self):
            self.closed = True

    db = FakeDB()
    captured = []
    monkeypatch.setattr(flow, "get_connection", lambda: db)
    monkeypatch.setattr(
        ui, "verified_apply_destination",
        lambda job, conn: (
            "https://example.wd5.myworkdayjobs.com/en-US/jobs/apply"
            if job["id"] == 42 and conn is db else None
        ),
    )
    def reserve(**kwargs):
        captured.append(kwargs)
        return {"provider": kwargs["provider"], "route_id": "native-route-1"}

    monkeypatch.setattr(router, "select_provider", reserve)
    result = flow._reserve_native_route_for_approval(
        job_id=42, preflight_package_id="package-42", click_key="click-42",
    )
    assert result["provider"] == "NATIVE"
    assert captured[0]["job_id"] == 42
    assert captured[0]["preflight_package_id"] == "package-42"
    assert captured[0]["verified_employer_url"].startswith("https://example.wd5")
    assert captured[0]["idempotency_key"].startswith("native-approved-")
    assert db.closed is True


def test_missing_verified_employer_link_fails_closed(monkeypatch):
    from app import application_provider_router_v1 as router
    from app import application_provider_ui_v1 as ui

    class FakeDB:
        def execute(self, query, args=()):
            if query.startswith("PRAGMA"):
                return None
            return SimpleNamespace(fetchone=lambda: {"id": 42, "title": "HR Analyst"})

        def close(self):
            pass

    monkeypatch.setattr(flow, "get_connection", lambda: FakeDB())
    monkeypatch.setattr(ui, "verified_apply_destination", lambda job, connection: None)
    monkeypatch.setattr(
        router, "select_provider",
        lambda **kwargs: pytest.fail("Must not reserve an unverifiable destination"),
    )
    with pytest.raises(PermissionError, match="verified employer"):
        flow._reserve_native_route_for_approval(
            job_id=42, preflight_package_id="package-42", click_key="click-42"
        )


def test_competing_provider_route_prevents_execution_mandate(monkeypatch):
    from app import application_execution_mandate_v1 as mandate

    monkeypatch.setattr(
        flow, "flow_for_job",
        lambda _: {
            "state": "READY_FOR_APPROVAL",
            "package_review": {"preflight_package_id": "package-42", "review_digest": "a" * 64},
            "artifacts": {"preflight": {
                "preflight_package_id": "package-42", "cover_letter_decision": "NOT_REQUIRED"
            }},
        },
    )
    monkeypatch.setattr(
        flow, "_reserve_native_route_for_approval",
        lambda **kwargs: (_ for _ in ()).throw(
            FileExistsError("Exact opening owned by TSENTA")
        ),
    )
    monkeypatch.setattr(
        mandate, "approve_package",
        lambda **kwargs: pytest.fail("No authority after conflicting provider lock"),
    )
    with pytest.raises(FileExistsError, match="TSENTA"):
        flow.approve_and_submit_for_job(42)


def test_non_native_route_cannot_create_mandate(monkeypatch):
    from app import application_execution_mandate_v1 as mandate

    monkeypatch.setattr(
        flow, "flow_for_job",
        lambda _: {
            "state": "READY_FOR_APPROVAL",
            "package_review": {"preflight_package_id": "package-42", "review_digest": "a" * 64},
            "artifacts": {"preflight": {
                "preflight_package_id": "package-42", "cover_letter_decision": "NOT_REQUIRED"
            }},
        },
    )
    monkeypatch.setattr(
        flow, "_reserve_native_route_for_approval",
        lambda **kwargs: {"provider": "TSENTA"},
    )
    monkeypatch.setattr(
        mandate, "approve_package",
        lambda **kwargs: pytest.fail("Must not create mandate for TSENTA"),
    )
    with pytest.raises(PermissionError, match="Native execution"):
        flow.approve_and_submit_for_job(42)
