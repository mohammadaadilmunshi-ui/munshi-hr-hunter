from __future__ import annotations

import inspect

from app import application_provider_ui_v1 as ui
from app import product_pages, unified_job_workspaces_v1


def eligible_job(source="manual"):
    return {
        "id": 18, "source": source, "status": "found",
        "primary_decision": "ELIGIBLE", "already_applied": 0,
        "hard_rejection_reason": None,
        "apply_url": "https://jobs.lever.co/example/verified-role",
    }


def ready_flow(route=None, *, status=""):
    return {
        "state": status, "provider_route": route,
        "artifacts": {"preflight": {
            "preflight_package_id": "preflight-ready-1",
            "preflight_status": "READY_TO_APPLY",
        }},
    }


def test_ui_only_offers_tsenta_after_real_backend_connection():
    context = {
        "job": eligible_job(), "flow": ready_flow(),
        "employer_apply_url": "https://jobs.lever.co/example/verified-role",
    }
    disconnected = ui.provider_readiness(**context, tsenta_backend_connected=False)
    assert disconnected["can_select_native"] is True
    assert disconnected["can_select_tsenta"] is False
    assert disconnected["package_id"] == "preflight-ready-1"
    connected = ui.provider_readiness(**context, tsenta_backend_connected=True)
    assert connected["can_select_tsenta"] is True


def test_missing_evidence_or_preflight_disables_both_engines():
    context = {"job": eligible_job(), "flow": ready_flow()}
    missing_link = ui.provider_readiness(
        **context, employer_apply_url=None, tsenta_backend_connected=True,
    )
    assert not missing_link["can_select_native"]
    assert not missing_link["can_select_tsenta"]
    missing_package = ui.provider_readiness(
        job=eligible_job(),
        flow=ready_flow() | {
            "artifacts": {"preflight": {
                "preflight_package_id": "preflight-1",
                "preflight_status": "NEEDS_INPUT",
            }}
        },
        employer_apply_url="https://jobs.lever.co/example/verified-role",
        tsenta_backend_connected=True,
    )
    assert not missing_package["can_select_native"]


def test_prior_submission_or_existing_owner_disables_engine_change():
    destination = "https://jobs.lever.co/example/verified-role"
    submitted = ui.provider_readiness(
        job=eligible_job(), flow=ready_flow(status="AWAITING_RESPONSE"),
        employer_apply_url=destination, tsenta_backend_connected=True,
    )
    assert not submitted["can_select_native"]
    assert not submitted["can_select_tsenta"]

    locked = ui.provider_readiness(
        job=eligible_job(),
        flow=ready_flow(route={"provider": "NATIVE", "state": "PREPARATION"}),
        employer_apply_url=destination, tsenta_backend_connected=True,
    )
    assert locked["route_exists"] is True
    assert not locked["can_select_native"]


def test_readonly_provider_progress_distinguishes_reported_from_verified():
    no_receipt = ui.provider_progress(
        {"provider": "TSENTA", "state": "PROVIDER_SUBMITTED"}
    )
    assert no_receipt["status"] == "Provider reports submitted"
    assert no_receipt["is_employer_confirmed"] is False
    confirmed = ui.provider_progress({
        "provider": "NATIVE", "state": "EMPLOYER_CONFIRMED",
        "receipt_evidence_digest": "a" * 64,
    })
    assert confirmed["is_employer_confirmed"] is True
    assert confirmed["receipt"] == "Independently verified"


def test_exact_signed_linkedin_url_only(monkeypatch):
    job = eligible_job(source="linkedin")
    monkeypatch.setattr(ui, "latest_verified_for_job", lambda *_args: None)
    assert ui.verified_apply_destination(job, object()) is None
    monkeypatch.setattr(ui, "latest_verified_for_job", lambda *_args: {
        "employer_apply_url": "https://jobs.lever.co/example/verified-role"
    })
    assert ui.verified_apply_destination(job, object()) == (
        "https://jobs.lever.co/example/verified-role"
    )


def test_dashboard_wiring_is_original_product_workspace():
    product_src = inspect.getsource(product_pages.dashboard)
    workspace_src = inspect.getsource(unified_job_workspaces_v1.render_applications)
    assert "render_dashboard_summary()" in product_src
    assert "render_workspace(selected.job_id)" in workspace_src
    assert 'provider_progress(flow.get("provider_route"))' in inspect.getsource(product_pages._job_detail_dialog)


def test_learning_summary_fails_closed_when_table_does_not_exist(hunter_db):
    del hunter_db
    assert ui.learning_summary() == {"form_fields": 0, "provider_events": 0}



def test_learning_summary_counts_only_sanitized_owner_records(hunter_db):
    del hunter_db
    from app import tsenta_observation_v1 as observations
    fields = observations.observations_from_review(
        provider_application_id="synthetic-only",
        ats_type="brassring",
        review={
            "awaitingReview": True,
            "fields": [{"label": "State or Province", "kind": "text", "value": None}],
        },
    )
    status = observations.observation_from_status(
        provider_application_id="synthetic-only",
        ats_type="brassring",
        status={"status": "PAUSED"},
    )
    observations.persist_observations([*fields, status])
    assert ui.learning_summary() == {"form_fields": 1, "provider_events": 1}

def test_streamlit_workspace_shows_disabled_tsenta_when_backend_absent(monkeypatch):
    """Exercise the actual initial-dashboard control tree without touching DB."""
    from streamlit.testing.v1 import AppTest

    from app import database
    from app import product_application_flow_v2 as flow

    class FakeDB:
        def execute(self, _sql, _args):
            class Cursor:
                def fetchone(self):
                    return eligible_job() | {
                        "id": 8,
                        "apply_url": "https://jobs.lever.co/example/verified-role",
                    }
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", FakeDB)
    monkeypatch.setattr(flow, "flow_for_job", lambda *_a, **_kw: ready_flow())
    app = AppTest.from_string(
        "from app.application_provider_ui_v1 import render_workspace\n"
        "render_workspace(8)\n"
    ).run(timeout=15)
    assert not list(app.exception)
    assert app.radio[0].options == ["MUNSHI Native", "Tsenta"]
    assert app.button[0].label == "Save engine selection"
    assert app.button[0].disabled is True

    app.radio[0].set_value("Tsenta").run()
    assert not list(app.exception)
    assert app.button[0].disabled is True

def test_streamlit_reserved_route_is_readonly_and_has_separate_receipt(monkeypatch):
    from streamlit.testing.v1 import AppTest

    from app import database
    from app import product_application_flow_v2 as flow

    class FakeDB:
        def execute(self, _sql, _args):
            class Cursor:
                def fetchone(self):
                    return eligible_job() | {"id": 11}

            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(database, "get_connection", FakeDB)
    monkeypatch.setattr(
        flow, "flow_for_job",
        lambda *_a, **_kw: ready_flow({
            "provider": "TSENTA", "state": "PROVIDER_SUBMITTED",
            "attempt_count": 1,
        }),
    )
    app = AppTest.from_string(
        "from app.application_provider_ui_v1 import render_workspace\n"
        "render_workspace(11)\n"
    ).run(timeout=15)
    assert not list(app.exception)
    assert len(app.radio) == 0
    assert len(app.button) == 0
    assert "Not independently verified" in [metric.value for metric in app.metric]


def test_streamlit_dashboard_summary_is_readonly(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(ui, "learning_summary", lambda: {
        "form_fields": 14, "provider_events": 3,
    })
    app = AppTest.from_string(
        "from app.application_provider_ui_v1 import render_dashboard_summary\n"
        "render_dashboard_summary()\n"
    ).run(timeout=15)
    assert not list(app.exception)
    assert any(m.label == "Learning signals" and str(m.value) == "17"
               for m in app.metric)
    assert not list(app.button)
