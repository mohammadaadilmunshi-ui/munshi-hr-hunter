"""Regression checks for the original-crown favicon and installable-app assets."""
from __future__ import annotations

import json
from pathlib import Path
import struct

from scripts.package_streamlit_theme import package

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def png_size(filename: str) -> tuple[int, int]:
    content = (STATIC / filename).read_bytes()
    assert content[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", content[16:24])


def test_apple_and_pwa_icons_have_correct_dimensions():
    assert png_size("apple-touch-icon.png") == (180, 180)
    assert png_size("icon-192.png") == (192, 192)
    assert png_size("icon-512.png") == (512, 512)
    assert png_size("icon-maskable-512.png") == (512, 512)
    assert png_size("favicon-light.png") == (256, 256)
    assert png_size("favicon-dark.png") == (256, 256)
    assert (STATIC / "favicon.ico").read_bytes().startswith(b"\x00\x00\x01\x00")


def test_manifest_targets_files_served_by_streamlit():
    manifest = json.loads((STATIC / "manifest.json").read_text())
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    assert manifest["short_name"] == "MUNSHI"
    assert len(manifest["icons"]) == 3
    for item in manifest["icons"]:
        assert item["src"].startswith("/app/static/")
        assert (STATIC / item["src"].removeprefix("/app/static/")).is_file()


def test_brand_head_is_installed_once_and_theme_aware():
    page = "<html><head></head><body></body></html>"
    html = package(page, "<style>.test{color:#fff}</style>")
    again = package(html, "<style>.test{color:#fff}</style>")
    for output in (html, again):
        assert output.count('rel="apple-touch-icon"') == 1
        assert output.count('rel="manifest"') == 1
        assert output.count('(prefers-color-scheme: dark)') == 1
        assert output.count('(prefers-color-scheme: light)') == 1
        assert 'href="/app/static/apple-touch-icon.png"' in output


def test_dashboard_docker_exposes_only_curated_static_files():
    assert "enableStaticServing = true" in (ROOT / ".streamlit/config.toml").read_text()
    assert "COPY static ./static" in (ROOT / "Dockerfile").read_text()
    assert set(f.name for f in STATIC.iterdir()) == {
        "apple-touch-icon.png", "favicon.ico", "favicon-dark.png", "favicon-light.png",
        "icon-192.png", "icon-512.png", "icon-maskable-512.png", "manifest.json",
    }
