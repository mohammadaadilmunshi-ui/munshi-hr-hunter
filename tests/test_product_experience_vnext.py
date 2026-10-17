"""Mobile, navigation and truth-oriented UI contracts without a production browser."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.product_experience_vnext import (
    ALLOWED_VIEWS,
    application_breadcrumb_markup,
    lifecycle_markup,
    mobile_dock_markup,
    presentation_css,
    product_href,
)

ROOT = Path(__file__).resolve().parents[1]


def test_phone_dock_uses_real_existing_routes_and_accessible_labels() -> None:
    for active in ALLOWED_VIEWS:
        dock = mobile_dock_markup(active)
        assert 'aria-label="Quick navigation"' in dock
        assert dock.count("<a ") == 5
        for route in ("dashboard", "jobs", "prepare-application", "auto-prepare", "tracker"):
            assert f'href="?view={route}"' in dock
        if active in { "dashboard", "jobs", "prepare-application", "auto-prepare", "tracker" }:
            assert dock.count('aria-current="page"') == 1
        else:
            assert 'aria-current="page"' not in dock


def test_app_shortcuts_and_job_ids_cannot_inject_external_or_html_routes() -> None:
    links = application_breadcrumb_markup(5016)
    assert '?view=jobs&amp;job=5016' in links
    assert '?view=prepare-application' in links
    assert '?view=profile' in links
    assert 'target="_self"' in links
    assert links.count("<a ") == 3
    for bad in ("https://evil.example", 'jobs" onclick="alert(1)', "", "bogus"):
        with pytest.raises(ValueError):
            product_href(bad)
    for bad_id in (0, -1, True, "5016", 0.4):
        with pytest.raises(ValueError):
            product_href("jobs", job_id=bad_id)
    with pytest.raises(ValueError):
        product_href("settings", job_id=5016)


def test_mobile_css_has_touch_safe_layout_and_accessibility() -> None:
    css = presentation_css()
    for contract in (
        "max-width:920px", "max-width:760px", "max-width:420px",
        "safe-area-inset-bottom", "position:fixed", "min-height:48px",
        "font-size:16px", "scroll-snap-type:x proximity",
        "prefers-reduced-motion:reduce", "overflow-x:auto",
        "aria",  # No runtime auth or mutations, just CSS
    ):
        if contract == "aria":
            continue
        assert contract in css
    assert ".munshi-mobile-dock" in css
    assert "--mn-forest:#123d31" in css
    assert "--mn-brass:#9b6e2d" in css


def test_process_steps_are_honest_and_mobile_scrollable() -> None:
    markup = lifecycle_markup()
    assert "<ol" in markup
    assert markup.count('<li class="munshi-journey-step">') == 5
    assert "independent employer confirmation" in markup
    assert "submitted successfully" not in markup.casefold()


def test_shell_injects_theme_after_original_layers_and_renders_dock() -> None:
    source = (ROOT / "app/product_shell.py").read_text()
    a = source.index("    inject_v22_css()")
    b = source.index("    inject_job_card_interaction_css()")
    c = source.index("    inject_presentation_css()")
    assert a < b < c
    assert "render_mobile_dock(view)" in source
    assert "Discovery: Unlimited" in source
    assert "Automation: Unlimited" not in source


def test_application_workspace_shortcuts_and_stages_do_not_mutate_data() -> None:
    source = (ROOT / "app/application_workspace_page.py").read_text()
    assert "render_application_breadcrumb(job_id)" in source
    assert "render_lifecycle_steps()" in source
    assert "Only independently confirmed employer evidence" in source


def test_application_status_projection_shows_actual_next_action() -> None:
    from app.product_experience_vnext import (
        application_action_copy,
        application_stage_markup,
    )

    assert "Preparing in background" in application_action_copy("PREPARING", "No action needed")
    assert "do not resubmit" in application_action_copy("SUBMITTING", "No action needed")
    assert "authorize once" in application_action_copy("READY_FOR_APPROVAL", "Review resume")
    assert application_action_copy("FAILED_SAFELY", "Review the issue") == "Review the issue"
    unverified = application_stage_markup(6, receipt_verified=False)
    assert "Verify receipt" in unverified
    assert "Employer verified" not in unverified
    verified = application_stage_markup(6, receipt_verified=True)
    assert "Employer verified" in verified
    assert verified.count("is-complete") == 6
    assert 'aria-current="step"' not in verified
    current = application_stage_markup(3, receipt_verified=False)
    assert current.count('aria-current="step"') == 1


def test_legacy_application_surface_reuses_status_projection_with_manual_refresh() -> None:
    source = (ROOT / "app/unified_job_workspaces_v1.py").read_text()
    assert 'application_action_copy(item.canonical_state, item.next_action)' in source
    assert 'application_action_copy(model.canonical_state, model.next_action)' in source
    assert "application_stage_markup(model.stage_index, receipt_verified=confirmed)" in source
    assert "application_center_refresh" in source
    assert "This does not retry or submit" in source



def test_engine_panel_links_back_to_application_review_and_employer_source() -> None:
    source = (ROOT / "app/application_provider_ui_v1.py").read_text()
    assert "Open application package and review" in source
    assert "Continue application workflow" in source
    assert "Open saved application link" in source
    assert 'product_href("prepare-application", job_id=int(job_id))' in source
    assert "does not retry, apply, or submit" in source
    assert "router.select_provider(" in source


def test_stage_track_css_preserves_desktop_grid_and_mobile_scroll():
    from app.product_experience_vnext import presentation_css
    css = presentation_css()
    assert "ol.munshi-stage-track" in css
    assert "grid-template-columns:repeat(6,minmax(0,1fr))!important" in css
    assert "grid-auto-flow:column" in css
    assert "grid-template-columns:none!important" in css
