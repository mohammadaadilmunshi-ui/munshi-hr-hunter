from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.product_state import tracker_status, valid_view
from app.product_ui import pastel_for


ROOT = Path(__file__).resolve().parent.parent
PRIMARY_VIEWS = (
    ("Dashboard", "product_nav_dashboard"),
    ("Browse jobs", "product_nav_jobs"),
    ("Auto Prepare", "product_nav_auto-prepare"),
    ("Tracker", "product_nav_tracker"),
    ("Profile", "product_nav_profile"),
    ("Research", "product_nav_research"),
    ("Settings", "product_nav_settings"),
)


@pytest.mark.parametrize(("label", "key"), PRIMARY_VIEWS)
def test_product_primary_views_render_without_exception(label: str, key: str) -> None:
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.run()
    navigation = next(button for button in app.button if button.label == label)
    navigation.click()
    app.run()
    assert not app.exception, label


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
    app.run()
    next(button for button in app.button if button.label == "Settings").click()
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
    app.run()
    next(button for button in app.button if button.label == "Auto Prepare").click()
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
    assert "@media (max-width:720px)" in css_source
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
