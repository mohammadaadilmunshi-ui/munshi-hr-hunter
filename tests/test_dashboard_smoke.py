from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent
PAGES = (
    "Overview",
    "Historical Intelligence",
    "Source Health",
    "Adapter Coverage",
    "Targeting",
    "Job Explorer",
    "Query Performance",
    "Queue / Actions",
    "Credentials",
    "System / Diagnostics",
    "Storage",
    "Backups",
)


@pytest.mark.parametrize("page", PAGES)
def test_operations_dashboard_page_renders_without_exception(page: str) -> None:
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.run()
    navigation = next(button for button in app.sidebar.button if button.label == page)
    navigation.click()
    app.run()
    assert not app.exception


def test_dashboard_render_does_not_change_usajobs_runtime_policy() -> None:
    from app.database import get_connection

    connection = get_connection()
    try:
        before = connection.execute(
            "SELECT enabled FROM source_health WHERE source_name='USAJobs'"
        ).fetchone()[0]
    finally:
        connection.close()

    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
    app.run()
    next(button for button in app.sidebar.button if button.label == "Source Health").click()
    app.run()

    connection = get_connection()
    try:
        after = connection.execute(
            "SELECT enabled FROM source_health WHERE source_name='USAJobs'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert before == after


def test_public_branding_and_overview_do_not_market_usajobs() -> None:
    dashboard_source = (ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")
    operations_source = (ROOT / "app" / "operations_dashboard.py").read_text(encoding="utf-8")
    overview_source = operations_source.split("def _overview()", 1)[1].split("def _historical_intelligence()", 1)[0]
    assert "MUNSHI Apply" in dashboard_source
    assert "MUNSHI APPLY" in operations_source
    assert "AADIL HR HUNTER" not in dashboard_source
    assert "AADIL HR HUNTER" not in operations_source
    assert "USAJobs" not in overview_source


def test_job_explorer_hides_machine_evidence_until_advanced_expander() -> None:
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

    labels = usajobs_status_labels(
        configured=True,
        runtime_enabled=False,
        connection_verified=True,
    )
    assert labels == {
        "credentials": "Configured",
        "connection": "Verified",
        "runtime": "Disabled",
    }
