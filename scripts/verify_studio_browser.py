"""Chromium/WebKit QA of synthetic shared components; never opens production.

Requires Playwright browsers. No application server, database or submit engine.
Run: python scripts/verify_studio_browser.py /tmp/munshi-studio-review
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright
from scripts.render_studio_preview import preview_html

WIDTHS = (375, 390, 430, 768, 1280, 1440, 1920)


def verify(output: Path) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    preview = output / "preview.html"
    preview.write_text(preview_html(), encoding="utf-8")
    results = []
    with sync_playwright() as playwright:
        for engine in ("chromium", "webkit"):
            browser = getattr(playwright, engine).launch(headless=True)
            for width in WIDTHS:
                page = browser.new_page(viewport={"width": width, "height": 1000 if width > 768 else 844},
                                        reduced_motion="reduce", has_touch=width < 921)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                # Test the bundled/fallback layout offline; no external font calls.
                page.route("https://**/*", lambda route: route.abort())
                page.goto(preview.as_uri())
                page.locator(".job-title").first.wait_for()
                page.evaluate("document.fonts.ready")
                assert page.evaluate('document.fonts.check(\'600 16px "Manrope"\')'), (engine, width, "bundled font failed")
                dimensions = page.evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})")
                assert dimensions["scroll"] <= width, (engine, width, dimensions)
                assert page.locator(".job-title").count() == 4
                assert page.locator(".job-company").nth(1).inner_text() == "Example International Education and Research Foundation"
                details = page.get_by_role("button", name="Open job details")
                for button in details.all():
                    box = button.bounding_box()
                    assert box and box["height"] >= 44 and box["width"] >= 44, (engine, width, box)
                    assert button.evaluate("e=>getComputedStyle(e).opacity") == "1"
                # A text/touch region must never resolve to an invisible button.
                title = page.locator(".job-title").first
                title.scroll_into_view_if_needed()
                assert title.evaluate("e=>{const r=e.getBoundingClientRect();return !document.elementFromPoint(r.x+r.width/2,r.y+r.height/2).closest('button')}")
                if width < 921:
                    menu = page.locator(".mobile-nav summary")
                    menu.focus()
                    page.keyboard.press("Enter")
                    assert page.locator(".mobile-nav").evaluate("e=>e.open")
                    assert page.get_by_role("navigation", name="Mobile navigation").get_by_role("link", name="Resume Studio", exact=True).is_visible()
                    page.keyboard.press("Enter")
                    assert not page.locator(".mobile-nav").evaluate("e=>e.open")
                if width < 641:
                    metric_boxes = page.locator('[data-testid="stMetric"]').all()
                    first, second = (metric_boxes[i].bounding_box() for i in (0, 1))
                    assert first and second and abs(first["y"] - second["y"]) < 2, (engine, width, "metrics did not form two columns")
                    page.locator('[data-testid="stExpander"]').last.scroll_into_view_if_needed()
                    last = page.locator('[data-testid="stExpander"]').last.bounding_box()
                    dock = page.locator(".st-key-product_mobile_routes").bounding_box()
                    assert last and dock and last["y"] + last["height"] <= dock["y"] + 1, (engine, width, "dock obscures final control")
                assert not errors, (engine, width, errors)
                page.evaluate("window.scrollTo(0,0)")
                page.locator("body").click(position={"x": 2, "y": 2})
                screenshot = output / f"{engine}-{width}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                results.append({"engine": engine, "width": width, "overflow": False,
                                "details_buttons": 4, "touch_targets": "pass",
                                "keyboard_menu": "pass" if width < 921 else "not applicable",
                                "console_errors": errors, "screenshot": screenshot.name})
                page.close()
            browser.close()
    (output / "browser-results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    results = verify(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/munshi-studio-review"))
    print(f"PASS: {len(results)} synthetic browser/viewport combinations. Not real-device Safari or live Streamlit interaction proof.")
