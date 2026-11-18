"""Mobile-only CSS regressions for Safari, without modifying the MUNSHI theme."""
from __future__ import annotations

from app.product_experience_vnext import presentation_css, mobile_dock_markup


def test_mobile_css_keeps_existing_brand_tokens_and_navigation() -> None:
    css = presentation_css()
    assert "--mn-forest:#123d31" in css
    assert "--mn-cream:#f6f7f4" in css
    assert "--mn-brass:#9b6e2d" in css
    assert ".munshi-mobile-dock" in css
    assert ".mobile-nav[open] nav" in css
    assert "env(safe-area-inset-bottom" in css


def test_ios_forms_tabs_dialogs_and_touch_targets_are_responsive() -> None:
    css = presentation_css()
    assert "@media(max-width:760px)" in css
    assert '[data-testid="stTabs"] [role="tablist"]' in css
    assert "overflow-x:auto" in css
    assert '[data-baseweb="select"] input' in css
    assert "font-size:16px!important" in css
    assert '[data-testid="stDialog"] [role="dialog"]' in css
    assert "width:calc(100dvw - 1rem)!important" in css
    assert ".mobile-nav-link{min-height:48px" in css
    assert "-webkit-overflow-scrolling:touch" in css


def test_mobile_dock_links_remain_first_party() -> None:
    html = mobile_dock_markup("dashboard")
    assert "munshi-mobile-dock" in html
    assert 'href="?view=dashboard"' in html
    assert "target=\"_self\"" in html
    assert "https://" not in html
