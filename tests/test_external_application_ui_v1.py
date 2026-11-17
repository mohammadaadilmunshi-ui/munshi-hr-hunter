from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def restore_synthetic_application_provider_fixtures():
    """AppTest fragments require persistent fakes; restore them after each test."""
    from app import application_view_model_v1 as native
    from app import external_application_tracker_v1 as external
    from app import external_tracker_sync_background_v1 as background
    original = (
        native.application_views, external.external_application_views,
        background.request_sync, background.read_status,
    )
    try:
        yield
    finally:
        (native.application_views, external.external_application_views,
         background.request_sync, background.read_status) = original


def _text(app: AppTest) -> str:
    values = []
    for group in ("markdown", "text", "caption", "info", "warning", "success"):
        values.extend(str(element.value) for element in getattr(app, group, []))
    return "\n".join(values)


def test_external_application_appears_in_original_application_center(tmp_path: Path):
    script = tmp_path / "external_application_ui.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
from app import application_view_model_v1 as native
from app import external_application_tracker_v1 as external
from app import unified_job_workspaces_v1 as workspace

tracked = external.ExternalApplicationView(
    external_key='a'*64,
    company='Healthcare Example',
    title='Recruitment Analyst',
    canonical_state='INTERVIEWING',
    human_state='Interviewing · provider reported',
    state_group='interviewing',
    last_update='2026-10-22T12:00:00Z',
    date_applied='2026-10-19',
    status_history=({'status':'APPLIED','at':'2026-10-19'},),
    application_id='external:'+'a'*64,
)
page = SimpleNamespace(_show_action_feedback=lambda: None)
native_workspace = SimpleNamespace()
native.application_views = lambda limit=None: []
external.external_application_views = lambda limit=5000, stages=None: [tracked]
workspace.render_applications(page, native_workspace)
""",
        encoding="utf-8",
    )
    app = AppTest.from_file(str(script), default_timeout=30).run()
    assert not app.exception
    content = _text(app)
    assert "Healthcare Example" in content
    assert "Recruitment Analyst" in content
    assert "Interviewing" in content
    assert all("Submit application" not in str(button.label) for button in app.button)
    options = [str(value) for widget in app.selectbox for value in widget.options]
    assert "Interviewing" in options
    assert any(button.label == "Open application" for button in app.button), [button.label for button in app.button]
    open_button = next(button for button in app.button if button.label == "Open application")
    open_button.click().run()
    assert not app.exception
    assert "Application timeline" in _text(app)


def test_external_details_never_display_native_submission_action(tmp_path: Path):
    script = tmp_path / "external_detail_ui.py"
    script.write_text(
        """
from types import SimpleNamespace
from unittest.mock import patch
import streamlit as st
from app import application_view_model_v1 as native
from app import external_application_tracker_v1 as external
from app import unified_job_workspaces_v1 as workspace

tracked = external.ExternalApplicationView(
    external_key='a'*64,
    company='Healthcare Example',
    title='Recruitment Analyst',
    canonical_state='INTERVIEWING',
    human_state='Interviewing · provider reported',
    state_group='interviewing',
    last_update='2026-10-22T12:00:00Z',
    date_applied='2026-10-19',
    status_history=({'status':'APPLIED','at':'2026-10-19'},),
    application_id='external:'+'a'*64,
)
st.query_params['external']='a'*64
page = SimpleNamespace(_show_action_feedback=lambda: None)
native_workspace = SimpleNamespace()
native.application_views = lambda limit=None: []
external.external_application_views = lambda limit=5000, stages=None: [tracked]
workspace.render_applications(page, native_workspace)
""",
        encoding="utf-8",
    )
    app = AppTest.from_file(str(script), default_timeout=30).run()
    assert not app.exception
    content = _text(app)
    assert "Application timeline" in content
    assert "not independent employer-receipt verification" in content
    labels = [str(button.label) for button in app.button]
    assert "Close" in labels
    assert "Approve & Submit" not in labels


def test_full_history_button_queues_read_only_sync_without_blocking_applications(
    tmp_path: Path,
):
    script = tmp_path / "tracker_full_history_ui.py"
    script.write_text(
        """
from types import SimpleNamespace
import streamlit as st
from app import application_view_model_v1 as native
from app import external_application_tracker_v1 as external
from app import external_tracker_sync_background_v1 as background
from app import unified_job_workspaces_v1 as workspace

native.application_views = lambda limit=None: []
external.external_application_views = lambda limit=5000, stages=None: []

def queue_only(mode):
    st.session_state["provider_requests"] = st.session_state.get("provider_requests",0) + 1
    st.session_state["requested_mode"] = mode
    return {"state":"QUEUED", "checked":0}

def status_only(mode):
    if st.session_state.get("requested_mode") == mode:
        return {"state":"QUEUED", "checked":0}
    return {"state":"IDLE", "checked":0}

background.request_sync = queue_only
background.read_status = status_only
page = SimpleNamespace(_show_action_feedback=lambda: None)
workspace.render_applications(page, SimpleNamespace())
""",
        encoding="utf-8",
    )
    app = AppTest.from_file(str(script), default_timeout=30).run()
    assert not app.exception
    button = next(
        widget for widget in app.button
        if widget.label == "↻ Synchronize application pipeline"
    )
    button.click().run()
    assert not app.exception
    content = _text(app)
    assert app.session_state["provider_requests"] == 1
    assert app.session_state["requested_mode"] == "history"
    assert "queued for readback" in content
    assert "No applications were sent" not in content  # request-only, no outcome claim
    assert "Neither action submits, retries or approves jobs" in content


def test_stage_filters_work_for_native_and_synced_applications():
    from types import SimpleNamespace
    from app.unified_job_workspaces_v1 import _filter_application_views

    def item(stage, group):
        return SimpleNamespace(
            archived=False, canonical_state=stage, state_group=group,
            company="Employer", title="Analyst", human_state=stage,
            next_action="Review update",
        )

    views = [
        item("INTERVIEW", "submitted"),
        item("OFFER", "submitted"),
        item("REJECTED", "submitted"),
        item("INTERVIEWING", "interviewing"),
    ]
    assert len(_filter_application_views(views, group="interviewing")) == 2
    assert len(_filter_application_views(views, group="offers")) == 1
    assert len(_filter_application_views(views, group="closed")) == 1
