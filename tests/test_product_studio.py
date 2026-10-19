"""Offline presentation tests. No dashboard server or production data access."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from streamlit.testing.v1 import AppTest

from app.product_studio import studio_css, workspace_location

ROOT = Path(__file__).resolve().parents[1]


def test_engine_panel_defers_reads_and_refreshes_truth_when_opened():
    app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from app.product_studio import engine_status_panel
def summary():
 st.session_state["summary_reads"] = st.session_state.get("summary_reads", 0) + 1
 st.markdown("Current engine observations")
with patch("app.application_provider_ui_v1.render_dashboard_summary", side_effect=summary):
 engine_status_panel()
''').run()
    assert not app.exception
    assert "summary_reads" not in app.session_state
    app.session_state["studio_engine_status_open"] = True
    app.run()
    assert not app.exception
    assert app.session_state["summary_reads"] == 1
    # AppTest does not serialize expander widget state on an unchanged run.
    app.session_state["studio_engine_status_open"] = True
    app.run()
    assert app.session_state["summary_reads"] == 2  # never cache readiness truth
    app.session_state["studio_engine_status_open"] = False
    app.run()
    assert app.session_state["summary_reads"] == 2


def test_details_require_explicit_button_and_have_no_hover_help():
    app = AppTest.from_string('''
import streamlit as st
from unittest.mock import patch
from types import SimpleNamespace
from app import product_pages as p
with patch.object(p,"_application_flow",return_value={"cta":"prepare"}), patch.object(p,"_job_destination",return_value=SimpleNamespace(apply_via="Employer website",source="Fixture",original_url="")):
 p._job_card({"id":81001,"title":"Very Long People Analytics and Human Resources Operations Role","company_name":"International Education and Research Foundation","location_raw":"New York, NY","hunter_score":96},key_prefix="studio_test")
''').run()
    assert not app.exception
    assert "_product_job_dialog_once" not in app.session_state
    details = app.button(key="job_open_studio_test_81001")
    assert not details.proto.help
    assert "International Education and Research Foundation" in app.markdown[0].value
    details.click().run()
    assert not app.exception
    assert app.session_state["_product_job_dialog_once"] == 81001


def test_navigation_escapes_labels_and_styles_keep_motion_focus_safe():
    assert "<script>" not in workspace_location("<script>alert(1)</script>")
    css = studio_css()
    for token in ("focus-visible", "prefers-reduced-motion", "safe-area-inset-bottom", "min-height:44px", "position:static!important", "opacity:1!important"):
        assert token in css
    assert "transition:all" not in css
    assert "data:font/woff2;base64," in css
    assert "fonts.googleapis.com" not in css


def test_synthetic_preview_does_not_need_database_or_backend():
    # Isolate imports as well as data: modules using `from ... import` retain
    # patched references, so a process boundary prevents contaminating other tests.
    result = subprocess.run([sys.executable, "-c", '''
from app import database
def prohibited(*args, **kwargs):
 raise AssertionError("Synthetic preview attempted database access")
database.get_connection = prohibited
from scripts.render_studio_preview import preview_tree
app = preview_tree()
assert not app.exception
assert len([b for b in app.button if b.label == "Open job details"]) == 4
assert len(app.metric) == 5
'''], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_dashboard_filters_and_engines_are_secondary_to_operational_metrics():
    source = (ROOT / "app/product_pages.py").read_text()
    dashboard = source.split("def dashboard()", 1)[1].split("def browse_jobs()", 1)[0]
    assert dashboard.index('key="product_dashboard_metrics"') < dashboard.index('_search_filters("dashboard")')
    assert "Top matches" in dashboard
    assert "engine_status_panel()" in dashboard


def test_resume_appearance_follows_title_and_preserves_checked_generation(monkeypatch):
    from app import resume_studio_page_v4 as v4
    from app import resume_studio_page_v5 as v5
    from app import resume_studio_page_v6 as v6

    calls = []
    original_html = v6.resume_v5.version_html
    original_checked = v4._generate_checked
    monkeypatch.setattr(v6, "_presentation_settings", lambda: calls.append("appearance") or {"font": "Arial"})
    monkeypatch.setattr(v5.quality_hotfix, "install", lambda: None)
    def render_v4(*, appearance_controls=None):
        calls.append("title")
        assert v4._generate_checked is v5._generate_checked
        appearance_controls()
        calls.append("canvas")
    monkeypatch.setattr(v4, "render", render_v4)
    v6.render()
    assert calls == ["title", "appearance", "canvas"]
    assert v6.resume_v5.version_html is original_html
    assert v4._generate_checked is original_checked


def test_dashboard_activity_and_counters_share_current_lifecycle_truth(monkeypatch):
    from app import product_application_flow_v2 as flow
    from app.product_studio import dashboard_snapshot, recent_activity_markup

    calls = []
    def rows(*, limit):
        calls.append(limit)
        return [{"job_id": 81001, "title": "<Long Role>",
                 "company_name": "International Education and Research Foundation",
                 "display_status": "Needs review", "updated_at": None,
                 "_flow_projection": {"state": "READY_FOR_APPROVAL"}}]
    monkeypatch.setattr(flow, "pipeline_rows", rows)
    summary, recent = dashboard_snapshot()
    assert calls == [None]
    assert summary["needs_approval"] == 1
    assert summary["employer_confirmed"] == summary["sent_24h"] == 0
    assert len(recent) == 1
    markup = recent_activity_markup(recent)
    assert "&lt;Long Role&gt;" in markup
    assert "International Education and Research Foundation" in markup
    assert '?view=prepare-application&amp;job=81001' in markup
    assert "Submitted" not in markup
