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
    from app import relay_application_dispatch_v1 as dispatcher
    from app import application_execution_mandate_v1 as mandate

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
