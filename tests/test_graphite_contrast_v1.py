"""Opt-in Graphite contrast rules, color safety and no workflow mutation."""
from __future__ import annotations

from pathlib import Path
import re

from app.product_studio import shell_css

ROOT = Path(__file__).resolve().parents[1]
DARK = ROOT / "app/assets/graphite_dark.css"
CONTRAST = ROOT / "app/assets/graphite_contrast.css"


def _lum(s: str) -> float:
    r, g, b = [int(s[k:k+2], 16) / 255 for k in (1, 3, 5)]
    vals = [v / 12.92 if v <= .04045 else ((v + .055)/1.055) ** 2.4
            for v in (r, g, b)]
    return sum(a*b for a, b in zip(vals, (.2126, .7152, .0722)))


def _ratio(fg: str, bg: str) -> float:
    a, b = sorted((_lum(fg), _lum(bg)))
    return (b + .05)/(a + .05)


def test_all_action_state_colors_are_legible():
    pairs = (
        ("#1B2026", "#DDE1E5"),   # platinum CTA
        ("#F0F0EE", "#292D34"),   # ordinary enabled buttons
        ("#BBC2CB", "#262A31"),   # disabled, still identifiable
        ("#F0F0EE", "#30343D"),   # selected navigation
        ("#F0F0EE", "#24272E"),   # dropdown popup/normal choices
        ("#F0F0EE", "#393F49"),   # hovered/selected choice
        ("#C5CBD4", "#24282F"),   # pending progress
        ("#C8E9D7", "#25372F"),   # completed progress
        ("#F2E4C6", "#3C352A"),   # current progress
        ("#142820", "#A4D8BB"),   # success marker
        ("#292419", "#E0C792"),   # current marker
        ("#F0F0EE", "#303740"),   # profile initials
        ("#BBC4CF", "#1D2026"),   # profile labels
    )
    assert all(_ratio(a, b) >= 4.5 for a, b in pairs)


def test_scope_and_style_order_preserve_existing_light_theme():
    css = CONTRAST.read_text()
    combined = shell_css()
    assert css in combined
    assert combined.index(DARK.read_text()) < combined.index(css)
    assert ':root{' not in css.replace(' ', '')
    assert re.search(r'^\s*(html\[data-munshi-theme="dark"\]|/\*)', css)
    assert "html[data-munshi-theme=\"dark\"]" in css
    assert "filter:invert(" not in css
    assert "background:#fff" not in css.lower()
    assert "resume" in css.casefold()
    assert "document" in css.casefold()
    assert re.search(r'^(?:\.stApp|body|\[data-baseweb)', css, re.M) is None


def test_portal_surface_and_hover_are_outside_app_root():
    css = CONTRAST.read_text()
    assert 'html[data-munshi-theme="dark"] body :is(' in css
    assert '[role="listbox"]' in css
    assert 'li[role="option"]' in css
    assert '[aria-selected="true"]' in css
    assert '[aria-disabled="true"]' in css
    assert ':focus-visible' in css


def test_button_states_cover_internal_text_and_do_not_change_callbacks():
    css = CONTRAST.read_text()
    assert '.stButton>button[kind="primary"]' in css
    assert '[data-testid="stButton"]>button:not([kind="primary"]):not(:disabled)' in css
    assert 'button:disabled' in css
    assert '[class*="st-key-product_route_"][class*="_selected"]' in css
    assert '-webkit-text-fill-color' in css
    assert 'onClick' not in css
    assert 'pointer-events:none' not in css


def test_application_steps_and_profile_custom_text_are_covered():
    css = CONTRAST.read_text()
    for marker in (
        ".munshi-stage.is-complete", ".munshi-stage.is-current",
        ".munshi-stage-marker", ".munshi-journey-step",
        ".profile-avatar", ".profile-logo-fallback",
        ".profile-mini-heading", ".profile-skill-label",
        ".profile-section-icon.edu", ".profile-section-icon.cert",
        '[data-testid="stSegmentedControl"]',
        '[data-testid="stBaseButton-segmented_control"]',
        '[data-testid$="Active"]',
    ):
        assert marker in css


def test_champagne_halo_uses_unmodified_original_crown_in_dark_header_only():
    css = CONTRAST.read_text()
    assert "Champagne Halo" in css
    assert "radial-gradient(circle at 28% 22%" in css
    assert "#E8D1AA" in css
    assert "html[data-munshi-theme=\"dark\"] .stApp .product-header .brand .brand-mark" in css
    assert ".brand-mark img" in css
    assert "prefers-reduced-motion:reduce" in css
    from PIL import Image
    original = Image.open(ROOT / "app/assets/munshi_crown.png")
    assert original.mode == "RGBA"
    assert original.getpixel((0, 0))[3] == 0
    assert "data-munshi-theme" not in (ROOT / "app/product_v22.py").read_text()
