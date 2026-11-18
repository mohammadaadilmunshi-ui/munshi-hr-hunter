"""System-aware, high-contrast loading experience: CSS, JS and overrides."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.appearance_theme_v1 import appearance_controller_html, early_theme_boot
from scripts.package_streamlit_theme import BOOT, STATUS, package


def _contrast(a: str, b: str) -> float:
    def lum(color: str) -> float:
        rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(x * w for x, w in zip(linear, (.2126, .7152, .0722)))
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + .05) / (lo + .05)


def test_loading_screen_has_aa_text_and_unchanged_light_palette():
    assert "#F6F7F4" in BOOT and "#123D31" in BOOT and "#44545B" in BOOT
    for bg, heading, caption in [
        ("#F6F7F4", "#123D31", "#44545B"),
        ("#111317", "#F0F0EE", "#C6C9D0"),
    ]:
        assert _contrast(bg, heading) >= 7.0
        assert _contrast(bg, caption) >= 7.0


def test_boot_is_original_and_system_dark_modes_are_scoped():
    html = package("<html><head></head><body></body></html>", "<style>.original{}</style>")
    assert html.count('id="munshi-boot-status"') == 1  # one body element
    assert STATUS in html
    assert "Opening your workspace" in html
    assert "background:var(--munshi-boot-bg)" in html
    assert "color:var(--munshi-boot-caption)" in html
    assert '@media (prefers-color-scheme: dark)' in html
    assert 'html[data-munshi-theme="dark"]' in html
    assert 'html[data-munshi-theme="light"]' in html
    assert html.index(early_theme_boot()) < html.index(BOOT)
    assert html.count("const observer=new MutationObserver") == 1
    assert package(html, "<style>.original{}</style>").count(STATUS) == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not available")
@pytest.mark.parametrize(
    ("saved", "system_dark", "expected", "changed_dark", "after_change"),
    [
        (None, False, "light", True, "dark"),
        (None, True, "dark", False, "light"),
        ("light", True, "light", False, "light"),
        ("dark", False, "dark", True, "dark"),
    ],
)
def test_prepaint_and_open_app_follow_system_unless_explicit_override(
    saved: str | None, system_dark: bool, expected: str, changed_dark: bool, after_change: str
):
    program = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { saved, systemDark, expected, changedDark, afterChange, early, controller } = JSON.parse(process.argv[1]);
let storage = saved;
const root = { dataset: {} };
const listeners = {};
const media = {
  matches: systemDark,
  addEventListener(type, fn) { if(type === 'change') this.change = fn; }
};
const ctx = {
  window: {
    localStorage: {
      getItem: () => storage,
      setItem: (_key, value) => { storage = value; }
    },
    matchMedia: () => media
  },
  document: {
    documentElement: root,
    querySelectorAll: () => [],
    addEventListener(type, fn) { listeners[type] = fn; }
  },
  MutationObserver: class { observe(){} },
  requestAnimationFrame: fn => fn(),
  Element: class {}
};
function run(markup) {
  vm.runInNewContext(markup.replace(/^<script>/, '').replace(/<\/script>$/, ''), ctx);
}
run(early);
assert.equal(root.dataset.munshiTheme, expected, 'prepaint');
run(controller);
assert.equal(root.dataset.munshiTheme, expected, 'controller');
media.matches = changedDark;
media.change();
assert.equal(root.dataset.munshiTheme, afterChange, 'OS theme change');
console.log('PASS');
"""
    data = {
        "saved": saved,
        "systemDark": system_dark,
        "expected": expected,
        "changedDark": changed_dark,
        "afterChange": after_change,
        "early": early_theme_boot(),
        "controller": appearance_controller_html(),
    }
    result = subprocess.run(["node", "-e", program, json.dumps(data)], text=True, capture_output=True, timeout=8)
    assert result.returncode == 0, result.stderr
