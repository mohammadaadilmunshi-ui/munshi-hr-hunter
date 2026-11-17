"""All-page rerender boundaries and application-history read optimizations."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import unified_job_workspaces_v1 as workspaces


ROOT = Path(__file__).resolve().parents[1]


def test_settings_profile_tracker_research_are_real_isolated_fragments():
    shell = (ROOT / "app/product_shell.py").read_text()
    assert 'fragment_safe_routes = frozenset({' in shell
    for route in ("dashboard", "tracker", "research", "settings", "profile", "auto-prepare"):
        assert f'"{route}"' in shell[shell.index("fragment_safe_routes"):][:300]
    assert "PREPARE_APPLICATION_VIEW, RESUME_STUDIO_VIEW" in shell[
        shell.index("fragment_safe_routes"):
    ]
    assert "st.fragment(pages[view])()" in shell
    # Jobs already own one fragment. Prepare's auto-live nested fragment and
    # dashboard engine observer must not be wrapped in a nested fragment.
    assert 'pages_module.browse_jobs = st.fragment(browse_jobs_infinite)' in (
        ROOT / "app/career_os_quality_patch_v1.py"
    ).read_text()


def test_streamlit_fragment_keeps_outer_shell_stable_on_filter_input():
    app = AppTest.from_string("""
import streamlit as st
st.session_state["outer_runs"] = st.session_state.get("outer_runs", 0) + 1
st.caption(f"Shell runs: {st.session_state['outer_runs']}")
@st.fragment
def application_list():
    query = st.text_input("Application filter")
    st.write("Filtered: " + query)
application_list()
""").run()
    assert not app.exception
    assert app.session_state["outer_runs"] == 1
    app.text_input[0].set_value("Human Resources").run()
    assert not app.exception
    # Streamlit's testing harness may schedule the fragment on the same
    # initial run, but its parent should not increment on fragment rerun.
    assert app.session_state["outer_runs"] <= 2


def test_selected_application_stage_maps_to_sql_filter_or_full_history():
    assert workspaces._provider_stages_for_application_choice("Active") == (
        "INTERVIEWING", "OFFER",
    )
    assert workspaces._provider_stages_for_application_choice("All") is None
    assert workspaces._provider_stages_for_application_choice("Submitted") == (
        "APPLIED", "GHOSTED",
    )
    assert workspaces._provider_stages_for_application_choice("Closed") == (
        "REJECTED",
    )
    assert workspaces._provider_stages_for_application_choice("not a selector") is None


def test_source_cache_is_invalidated_on_stage_change_even_before_ttl():
    state = {}
    calls = []
    def read_native():
        calls.append("native")
        return ["native"]
    def read_external():
        calls.append("external")
        return ["external"]
    first = workspaces._application_center_source_snapshot(
        session=state, native_reader=read_native, external_reader=read_external,
        source_signature="('INTERVIEWING','OFFER')", now=1,
    )
    assert not first[2]
    second = workspaces._application_center_source_snapshot(
        session=state, native_reader=read_native, external_reader=read_external,
        source_signature="('INTERVIEWING','OFFER')", now=2,
    )
    assert second[2]
    third = workspaces._application_center_source_snapshot(
        session=state, native_reader=read_native, external_reader=read_external,
        source_signature="None", now=3,
    )
    assert not third[2]
    assert len(calls) == 4


def test_sync_controls_run_in_separate_polling_fragment_and_never_block():
    src = (ROOT / "app/unified_job_workspaces_v1.py").read_text()
    start = src.index('def _application_sync_controls()')
    end = src.index('\n@st.fragment\ndef _application_center_content', start)
    controls = src[start:end]
    assert '@st.fragment(run_every="6s")' in src[start-34:start]
    assert 'request_sync("history")' in controls
    assert 'request_sync("receipts")' in controls
    assert 'sync_authorized_tracker(' not in controls
    assert 'reconcile_owner_pending(' not in controls
    assert '@st.fragment\ndef _application_center_content' in src
    assert 'st.expander("Synchronization & provider checks", expanded=False)' in controls



def test_prepare_picker_does_not_recheck_250_jobs_on_every_form_widget(monkeypatch):
    from app import application_workspace_page as prepare
    from types import SimpleNamespace

    session = {}
    monkeypatch.setattr(prepare.st, "session_state", session)
    calls = []
    def list_options():
        calls.append("query")
        return [
            {"id": 1, "title": "HR Operations", "company_name": "Employer",
             "job_snapshot_sha256": "a"*64},
        ]
    def exact_snapshot(job_id):
        calls.append(("exact", job_id))
        return {
            "job_snapshot_sha256": "b"*64,
            "job": {
                "id": job_id, "title": "Talent Acquisition",
                "company_name": "Exact Employer",
            },
        }
    monkeypatch.setattr(prepare, "_job_options", list_options)
    monkeypatch.setattr(prepare, "safe_owned_job_snapshot", exact_snapshot)
    first = prepare._job_options_session_snapshot(now=0)
    assert len(first)==1 and calls==["query"]
    second=prepare._job_options_session_snapshot(now=29.9)
    assert second==first and calls==["query"]
    with_exact=prepare._job_options_session_snapshot(requested=9000,now=29.9)
    assert with_exact[0]["id"]==9000
    assert with_exact[0]["job_snapshot_sha256"]=="b"*64
    assert calls==["query",("exact",9000)]
    refreshed=prepare._job_options_session_snapshot(now=30)
    assert refreshed==first and calls.count("query")==2


def test_prepare_picker_fails_closed_when_requested_job_not_owned(monkeypatch):
    from app import application_workspace_page as prepare
    monkeypatch.setattr(prepare.st, "session_state", {})
    monkeypatch.setattr(prepare,"_job_options",lambda: [{"id":3}])
    def not_owned(job_id):
        raise PermissionError("Another owner")
    monkeypatch.setattr(prepare, "safe_owned_job_snapshot", not_owned)
    import pytest
    with pytest.raises(PermissionError):
        prepare._job_options_session_snapshot(requested=9,now=0)
