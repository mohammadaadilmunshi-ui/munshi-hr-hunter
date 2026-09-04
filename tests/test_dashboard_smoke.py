from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.product_state import tracker_status, valid_view
from app.product_ui import esc, pastel_for


ROOT = Path(__file__).resolve().parent.parent
PRIMARY_VIEWS = (
    ("Dashboard", "dashboard"),
    ("Browse jobs", "jobs"),
    ("Auto Prepare", "auto-prepare"),
    ("Tracker", "tracker"),
    ("Profile", "profile"),
    ("Research", "research"),
    ("Settings", "settings"),
)


@pytest.mark.parametrize(("label", "route"), PRIMARY_VIEWS)
def test_product_primary_views_render_without_exception(label: str, route: str, hunter_db) -> None:
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.session_state["product_view"] = route
    app.run()
    assert not app.exception, label


@pytest.mark.parametrize(
    ("route", "state_key", "value"),
    (
        ("tracker", "product_tracker_tab", "Inbox"),
        ("profile", "product_profile_tab", "Cover letters"),
        ("profile", "product_profile_tab", "Profile details"),
        ("settings", "product_settings_section", "Automation"),
        ("settings", "product_settings_section", "Integrations"),
        ("settings", "product_settings_section", "Profile & defaults"),
        ("settings", "product_settings_section", "Credentials"),
        ("settings", "product_settings_section", "Advanced / System"),
    ),
)
def test_product_subroutes_render_without_exception(
    route: str, state_key: str, value: str, hunter_db,
) -> None:
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.session_state["product_view"] = route
    app.session_state[state_key] = value
    app.run()
    assert not app.exception, f"{route}: {value}"


def test_dashboard_render_does_not_change_usajobs_runtime_policy(hunter_db) -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health (source_name, source_tier, enabled)
            VALUES ('USAJobs', 1, 0)
            ON CONFLICT(source_name) DO UPDATE SET enabled=excluded.enabled
            """
        )
        connection.commit()
        before = connection.execute(
            "SELECT enabled FROM source_health WHERE source_name='USAJobs'"
        ).fetchone()[0]
    finally:
        connection.close()
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.session_state["product_view"] = "settings"
    app.run()
    connection = get_connection()
    try:
        after = connection.execute(
            "SELECT enabled FROM source_health WHERE source_name='USAJobs'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert before == after


def test_product_render_does_not_queue_work(hunter_db) -> None:
    from app.database import get_connection
    connection = get_connection()
    try:
        before = connection.execute("SELECT COUNT(*) FROM n8n_dispatch_queue").fetchone()[0]
    finally:
        connection.close()
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.session_state["product_view"] = "auto-prepare"
    app.run()
    connection = get_connection()
    try:
        after = connection.execute("SELECT COUNT(*) FROM n8n_dispatch_queue").fetchone()[0]
    finally:
        connection.close()
    assert before == after


def test_product_shell_and_routes_are_present() -> None:
    dashboard_source = (ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")
    shell_source = (ROOT / "app" / "product_shell.py").read_text(encoding="utf-8")
    css_source = (ROOT / "app" / "product_ui.py").read_text(encoding="utf-8")
    assert "MUNSHI Apply" in dashboard_source
    assert "initial_sidebar_state=\"collapsed\"" in dashboard_source
    assert "NAVIGATION" in shell_source
    assert "st.query_params" in shell_source
    assert "product_tracker_tab" in shell_source
    assert "product_profile_tab" in shell_source
    assert "product_settings_section" in shell_source
    assert "@media (max-width:640px)" in css_source
    assert "prefers-reduced-motion" in css_source
    assert "<nav class=\"product-nav\"" in shell_source
    assert "mobile-nav" in shell_source
    assert "min-width:680px" not in css_source
    assert "AADIL HR HUNTER" not in dashboard_source
    assert valid_view("tracker") == "tracker"
    assert valid_view("not-a-route") == "dashboard"


def test_tracker_status_is_truth_bound() -> None:
    assert tracker_status("completed") == "Prepared"
    assert tracker_status("application_ready") == "Prepared"
    assert tracker_status("submission_confirmed") == "Submitted"
    assert tracker_status("unknown_backend_state") == "Other"
    assert tracker_status(None, "processing") == "In progress"


def test_job_card_tone_is_deterministic_and_safe() -> None:
    assert pastel_for(123) == pastel_for(123)
    assert pastel_for(123) != ""
    assert esc('<img src=x onerror="alert(1)">') == "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;"


def test_product_detail_keeps_raw_machine_data_collapsed_and_settings_keep_advanced_access() -> None:
    source = (ROOT / "app" / "product_pages.py").read_text(encoding="utf-8")
    assert 'with st.expander("Advanced decision evidence", expanded=False):' in source
    assert 'with st.expander("Raw machine evidence", expanded=False):' in source
    assert '"Advanced / System"' in source
    assert '"System / Diagnostics"' in source
    assert "gmail_message_id" in (ROOT / "app" / "gmail_integration.py").read_text(encoding="utf-8")
    assert "placeholder preview" in source
    assert "artifact-line" not in source


def test_advanced_job_explorer_keeps_machine_evidence_collapsed() -> None:
    source = (ROOT / "app" / "operations_dashboard.py").read_text(encoding="utf-8")
    explorer = source.split("def _job_explorer()", 1)[1].split("def _query_performance()", 1)[0]
    renderer = source.split("def _render_decision_intelligence(", 1)[1].split("def _job_explorer()", 1)[0]
    advanced_marker = 'with st.expander("Advanced decision evidence", expanded=False):'
    raw_marker = 'with st.expander("Raw machine evidence", expanded=False):'
    assert advanced_marker in renderer
    assert raw_marker in renderer
    assert renderer.index(advanced_marker) < renderer.index(raw_marker) < renderer.index("st.json")
    assert "st.json" not in renderer.split(raw_marker, 1)[0]
    assert "Targeting evidence" in renderer
    assert "Location evidence" in renderer
    assert "Experience evidence" in renderer
    assert "Deduplication & provenance" in renderer
    assert "Delivery & automation" in renderer
    assert '"Company exclusion"' in explorer
    assert '"Role outside targeting"' in explorer
    assert '"Location not eligible"' in explorer


def test_usajobs_credentials_and_runtime_labels_are_independent() -> None:
    from app.credentials_page import usajobs_status_labels

    labels = usajobs_status_labels(configured=True, runtime_enabled=False, connection_verified=True)
    assert labels == {"credentials": "Configured", "connection": "Verified", "runtime": "Disabled"}
