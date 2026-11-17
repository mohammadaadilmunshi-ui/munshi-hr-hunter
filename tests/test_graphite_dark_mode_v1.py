"""Graphite & Platinum safety and presentation contracts; no production access."""
from __future__ import annotations

from pathlib import Path
import re

from app.appearance_theme_v1 import (
    STORAGE_KEY, appearance_controller_html, appearance_toggle_html,
    early_theme_boot,
)
from app.product_ui import DARK_JOB_PALETTES, PASTELS, dark_palette_for, pastel_for
from app.product_studio import shell_css
from scripts.package_streamlit_theme import package

ROOT = Path(__file__).resolve().parents[1]


def _luminance(hex_color: str) -> float:
    vals = [int(hex_color[i:i+2],16)/255 for i in (1,3,5)]
    vals = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4
            for v in vals]
    return sum(a*b for a,b in zip(vals, (.2126,.7152,.0722)))


def _contrast(foreground: str, background: str) -> float:
    a, b = sorted((_luminance(foreground), _luminance(background)))
    return (b+.05)/(a+.05)


def test_all_18_original_job_card_tones_keep_deterministic_identity():
    assert len(PASTELS) == len(DARK_JOB_PALETTES) == 18
    assert len(set(PASTELS)) == len(set(DARK_JOB_PALETTES)) == 18
    for job_id in range(1, 120):
        light = pastel_for(job_id)
        dark, accent = dark_palette_for(job_id)
        assert DARK_JOB_PALETTES[PASTELS.index(light)] == (dark, accent)
        assert pastel_for(job_id) == light  # no modification of canonical light


def test_all_job_card_titles_metadata_and_tone_accents_have_aa_contrast():
    for dark, accent in DARK_JOB_PALETTES:
        assert _contrast("#F0F0EE", dark) >= 4.5
        assert _contrast("#C6C9D0", dark) >= 4.5
        assert _contrast(accent, dark) >= 4.5


def test_theme_is_opt_in_and_keeps_paper_white():
    css = (ROOT / "app/assets/graphite_dark.css").read_text()
    assert css.count('html[data-munshi-theme="dark"]') >= 45
    assert css in shell_css()
    assert re.search(r'^:root\s*\{',css,re.M) is None
    # Streamlit's Glide table is canvas-painted in the light color system.
    # Only its canvas may use a bounded correction; no whole-app inversion.
    assert css.replace(" ", "").count("filter:invert(") == 1
    assert '[data-testid="stDataFrame"]' in css
    assert '[data-testid="stDataEditor"]' in css
    assert 'resume paper' in css
    assert "prefers-reduced-motion:reduce" in css
    assert "munshiOwlBlink" in css
    assert (ROOT / ".streamlit/config.toml").read_text().count('base = "light"') == 1


def test_mobile_menu_switch_semantics_and_desktop_access():
    mobile = appearance_toggle_html()
    desktop = appearance_toggle_html(settings=True)
    for html in (mobile, desktop):
        assert 'data-munshi-toggle' in html
        assert 'role="switch"' in html
        assert 'aria-checked="false"' in html
        assert 'Dark mode' in html
        assert 'Graphite &amp; Platinum' in html
        assert "munshi-owl-eye" in html
    shell = (ROOT / "app/product_shell.py").read_text()
    assert "{mobile_links}{appearance_toggle_html()}" in shell
    assert 'appearance_toggle_html(settings=True)' in shell


def test_prepaint_storage_preference_and_no_observer_in_boot():
    assert STORAGE_KEY == "munshi.appearance.v1"
    initial = package('<html><head></head><body><div id="root"></div></body></html>', '<style>body{color:green}</style>')
    assert early_theme_boot() in initial
    assert initial.index(early_theme_boot()) < initial.index('munshi-boot-status')
    assert "localStorage.getItem" in early_theme_boot()
    assert "addEventListener" not in initial
    assert "setTimeout" not in initial


def test_client_switch_is_statically_scoped_and_does_not_reload_or_submit():
    javascript = appearance_controller_html()
    assert javascript.count("document.addEventListener('click'") == 1
    assert "window.__munshiAppearanceInstalled" in javascript
    assert "window.localStorage.setItem(key" in javascript
    assert "requestAnimationFrame" in javascript
    assert 'event.preventDefault()' in javascript
    assert "window.location.reload" not in javascript
    assert "fetch(" not in javascript
    assert "XMLHttpRequest" not in javascript
    assert "Streamlit.setComponentValue" not in javascript
