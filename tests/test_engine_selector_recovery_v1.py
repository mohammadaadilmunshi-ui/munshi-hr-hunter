"""Engine-selection recovery regression tests without live provider writes."""
from __future__ import annotations

import inspect

import pytest

from app import application_provider_ui_v1 as ui
from app import application_workspace_page as workspace
from app import product_application_flow_v2 as flow


def test_unlocked_native_and_tsenta_visible_but_not_false_ready():
    state = ui.engine_selector_state(
        route=None,
        tsenta_backend={
            "authenticated_backend": True,
            "profile_read_verified": True,
            "execution_ready": False,
        },
    )
    assert state["labels"] == ("MUNSHI Native", "MUNSHI Relay")
    assert state["selected"] == "MUNSHI Native"
    assert state["locked"] is False
    assert state["can_submit_tsenta"] is False


def test_existing_dohler_native_route_visible_and_locked():
    state = ui.engine_selector_state(
        route={"provider": "NATIVE", "state": "PREPARATION"},
        tsenta_backend={"execution_ready": True},
    )
    assert state["selected"] == "MUNSHI Native"
    assert state["locked"] is True
    assert state["can_submit_tsenta"] is False
    assert "approval cannot be reused" in state["reason"]


def test_existing_tsenta_route_stays_tsenta_not_native():
    state = ui.engine_selector_state(
        route={"provider": "TSENTA", "state": "PAUSED"},
        tsenta_backend={"execution_ready": False},
    )
    assert state["selected"] == "MUNSHI Relay"
    assert state["locked"] is True


def test_relay_approval_routes_to_one_use_dispatch_not_native_mandate(
    monkeypatch,
):
    from app import application_execution_mandate_v1 as mandate
    from app import relay_application_dispatch_v1 as dispatcher

    monkeypatch.setattr(
        flow,"flow_for_job",lambda _job_id: {
            "package_review":{"review_digest":"a"*64},
            "artifacts":{"preflight":{"preflight_package_id":"package-1"}},
        },
    )
    monkeypatch.setattr(
        mandate,"approve_package",
        lambda **kwargs: pytest.fail("Must not authorize the Native worker"),
    )
    captured={}
    def dispatch(**kwargs):
        captured.update(kwargs)
        return {"status":"PROVIDER_SUBMITTED"}
    monkeypatch.setattr(dispatcher,"submit_from_dashboard",dispatch)
    result=flow.approve_and_submit_for_job(6198,execution_provider="TSENTA")
    assert result["status"]=="PROVIDER_SUBMITTED"
    assert captured=={
        "job_id":6198,"review_digest":"a"*64,
        "preflight_package_id":"package-1",
    }
    with pytest.raises(PermissionError):
        flow.approve_and_submit_for_job(
            6198,approval_source="STANDING_POLICY",
            execution_provider="TSENTA",
        )


def test_approval_workspace_has_explicit_engine_selection():
    source = inspect.getsource(workspace)
    assert "Choose the application submission engine" in source
    assert "execution_provider=chosen_engine" in source
    assert "disabled=not submit_enabled" in source


def test_locked_engine_is_visible_in_application_panel():
    source = inspect.getsource(ui.render_workspace)
    assert "application_provider_locked_engine_" in source
    assert "disabled=True" in source


def test_saved_relay_reservation_can_proceed_to_its_first_approval():
    route = {
        "provider":"TSENTA","state":"PREPARATION","attempt_count":0,
        "dispatch_claim_exists":False,
        "provider_status":None,"provider_application_id_sha256":None,
    }
    result = ui.engine_selector_state(
        route=route,tsenta_backend={"execution_ready":True},
    )
    assert result["selected"] == "MUNSHI Relay"
    assert result["locked"] is True  # do not allow switching provider freely
    assert result["can_submit_tsenta"] is True
    assert "existing reservation once" in result["reason"]
    source = inspect.getsource(workspace._render_complete_loop)
    assert "not selector['locked']" not in source[source.index("submit_enabled = ("):]


@pytest.mark.parametrize("changed",[
    {"dispatch_claim_exists":True},
    {"attempt_count":1},
    {"state":"FILLING"},
    {"state":"PAUSED"},
    {"state":"PROVIDER_SUBMITTED"},
    {"provider_status":"DISPATCH_CLAIMED"},
    {"provider_application_id_sha256":"a"*64},
    {"provider_submitted_at":"2026-10-17T01:00:00Z"},
    {"employer_confirmed_at":"2026-10-17T02:00:00Z"},
    {"receipt_method":"EMAIL"},
])
def test_relay_reservations_with_dispatch_or_receipt_evidence_are_locked(changed):
    route = {"provider":"TSENTA","state":"PREPARATION","attempt_count":0}
    route.update(changed)
    state=ui.engine_selector_state(
        route=route,tsenta_backend={"execution_ready":True},
    )
    assert state["locked"] is True
    assert state["can_submit_tsenta"] is False


def test_relay_reserved_route_not_submittable_when_backend_disconnects():
    state=ui.engine_selector_state(
        route={"provider":"TSENTA","state":"PREPARATION"},
        tsenta_backend={"execution_ready":False},
    )
    assert state["can_submit_tsenta"] is False
