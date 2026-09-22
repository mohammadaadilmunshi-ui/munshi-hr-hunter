from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import unified_job_workspaces_v1 as workspaces


ROOT = Path(__file__).resolve().parents[1]


def test_unified_layer_reuses_dashboard_job_card_renderer() -> None:
    source = (ROOT / "app" / "unified_job_workspaces_v1.py").read_text(encoding="utf-8")
    assert "product_pages._job_card" in source
    assert 'key_prefix="tracker_unified"' in source
    assert 'key_prefix="research_opportunity"' in source
    assert "job-card" not in source
    assert "score_ring" not in source


def test_research_is_decision_intelligence_not_duplicate_system_telemetry() -> None:
    source = (ROOT / "app" / "unified_job_workspaces_v1.py").read_text(encoding="utf-8")
    for legacy_normal_surface in (
        "Lifetime opportunity funnel",
        "Opportunities scanned",
        "Normalized records",
        "Eligible telemetry",
        "Source discovery performance",
        "Query performance",
        "Source and provider health",
    ):
        assert legacy_normal_surface not in source
    for decision_surface in (
        "Decision brief",
        "Best current opportunities",
        "Companies worth a closer look",
        "Role signals from your strongest matches",
        "Risks and eligibility evidence",
        "Settings → Advanced / System",
    ):
        assert decision_surface in source


def test_applications_are_an_application_center_instead_of_recent_job_search() -> None:
    source = (ROOT / "app" / "unified_job_workspaces_v1.py").read_text(encoding="utf-8")
    assert "st.tabs(" not in source
    assert "Phase 4" not in source
    assert "Phase 5" not in source
    assert "Phase 6" not in source
    assert "Phase 7" not in source
    assert "Choose another job" not in source
    assert "Application Center" in source
    assert "application_views(limit=None)" in source
    assert '"Application page"' in source
    for label in ("Needs you", "Preparing", "Applying", "Needs attention", "Submitted", "All"):
        assert f'"{label}"' in source
    assert "jobs[0]" not in source


def test_product_shell_installs_unified_workspaces_last() -> None:
    shell = (ROOT / "app" / "product_shell.py").read_text(encoding="utf-8")
    assert "install_unified_job_workspaces" in shell
    unified = shell.index("install_unified_job_workspaces(product_pages, application_workspace_page)")
    assert unified > shell.index("install_product_v22(product_pages)")
    assert unified > shell.index("install_provider_telemetry_window(product_pages)")
    assert unified < shell.index("pages: dict[str, Callable[[], None]]")


def test_tracker_footer_is_human_readable() -> None:
    text = workspaces._pipeline_footer(
        {
            "display_status": "Needs review",
            "final_ats_score": 93.8,
            "progress_percent": 72,
            "runtime_question_count": 2,
        }
    )
    assert text == "Needs review · ATS 94 · 72% prepared · 2 question(s) need attention"


def test_runtime_safe_card_grid_accepts_multiple_application_links(tmp_path: Path) -> None:
    script = tmp_path / "card_grid_app.py"
    script.write_text(
        """
from types import SimpleNamespace
import streamlit as st
from app.unified_job_workspaces_runtime_patch_v1 import _card_grid

def render_card(row, *, key_prefix):
    st.write(f\"{key_prefix}:{row['id']}\")

pages = SimpleNamespace(_job_card=render_card)
_card_grid(
    pages,
    [
        {'id': 101, 'title': 'Role A'},
        {'id': 102, 'title': 'Role B'},
        {'id': 103, 'title': 'Role C'},
    ],
    key_prefix='runtime-test',
    application_links=True,
)
""",
        encoding="utf-8",
    )
    # AppTest does not expose link_button as a query attribute in the pinned
    # Streamlit release. The regression we care about is the actual runtime
    # contract: rendering several job-specific application links must complete
    # without DuplicateElementKey/unsupported-argument/other Streamlit errors.
    app = AppTest.from_file(str(script), default_timeout=30).run()
    assert not app.exception


def test_empty_data_routes_render_without_runtime_exception(hunter_db) -> None:
    for view in ("tracker", "research", "prepare-application"):
        app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=30)
        app.session_state["product_view"] = view
        app.run()
        assert not app.exception, view
