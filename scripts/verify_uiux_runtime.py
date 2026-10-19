"""Browser acceptance of the real synthetic-only Streamlit runtime.

Refuses to click anything until the staging-only marker is present. Never point
this at production. Screenshots contain fictional fixtures, not production data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

VIEWS = ("dashboard", "jobs", "prepare-application", "auto-prepare", "tracker", "profile", "resume-studio", "research", "settings")
OBSERVE = """window.ux={cls:0,lcp:0,events:[]};
for(const type of ['layout-shift','largest-contentful-paint','event']){
 if(!PerformanceObserver.supportedEntryTypes.includes(type))continue;
 try{new PerformanceObserver(l=>l.getEntries().forEach(e=>{
  if(type==='layout-shift'&&!e.hadRecentInput)window.ux.cls+=e.value;
  if(type==='largest-contentful-paint')window.ux.lcp=e.startTime;
  if(type==='event'&&e.interactionId)window.ux.events.push(e.duration);
 })).observe({type,buffered:true,durationThreshold:16});}catch(e){}
}"""


def ready(page):
    page.locator("#uiux-runtime").wait_for(timeout=45000)
    assert "Synthetic staging" in page.locator("#uiux-runtime").inner_text()
    page.wait_for_function("!document.querySelector('[data-testid=stSkeleton]')", timeout=30000)
    errors = page.locator('[data-testid="stException"]').all_text_contents()
    assert not errors, errors


def changed(page, previous):
    page.wait_for_function("old=>document.querySelector('#uiux-runtime')?.dataset.runs!==old", arg=previous, timeout=30000)
    ready(page)


def run(url: str, output: Path, *, baseline: bool = False):
    output.mkdir(parents=True, exist_ok=True)
    results = {"url": url, "baseline": baseline, "routes": [], "interactions": []}
    with sync_playwright() as p:
        for engine in ("chromium", "webkit"):
            browser = getattr(p, engine).launch(headless=True)
            for width in (1440, 390):
                page = browser.new_page(viewport={"width": width, "height": 1000 if width > 900 else 844}, has_touch=width < 900)
                page.route("https://**/*", lambda route: route.abort())
                page.add_init_script(OBSERVE)
                console_errors = []
                page.on("pageerror", lambda error: console_errors.append(str(error)))
                for view in VIEWS:
                    started = time.perf_counter()
                    page.goto(url + "/?view=" + view, wait_until="domcontentloaded")
                    ready(page)
                    if view in {"dashboard", "jobs"}:
                        page.get_by_role("button", name="Save", exact=True).first.wait_for(timeout=30000)
                    if view == "dashboard":
                        page.locator('[data-testid="stMetricValue"]').first.wait_for(timeout=30000)
                    overflow = page.locator('[data-testid="stMain"]').evaluate("e=>e.scrollWidth-e.clientWidth")
                    if not baseline:
                        assert overflow <= 1, (engine, width, view, "horizontal overflow", overflow)
                    elapsed = time.perf_counter() - started
                    snapshot = f"{engine}-{width}-{view}.png"
                    page.screenshot(path=str(output / snapshot))
                    results["routes"].append({"engine": engine, "width": width, "view": view,
                        "ready_seconds": round(elapsed, 3),
                        "server_seconds": float(page.locator("#uiux-runtime").get_attribute("data-server-seconds")),
                        "vitals": page.evaluate("window.ux"), "overflow_px": overflow, "screenshot": snapshot})
                    print(json.dumps(results["routes"][-1]), flush=True)
                    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
                if width < 900 and not baseline:
                    page.goto(url + "/?view=jobs");ready(page)
                    page.get_by_role("button", name="Open job details", exact=True).first.wait_for()
                    page.locator('.job-title').first.tap()
                    assert page.get_by_role("dialog").count() == 0
                    page.get_by_role("button", name="Open job details", exact=True).first.tap()
                    page.get_by_role("dialog").wait_for()
                    page.get_by_role("button", name="Close", exact=True).first.tap()
                    page.get_by_role("dialog").wait_for(state="hidden")
                    page.locator('.st-key-product_mobile_routes').get_by_role("button", name="Home", exact=True).tap()
                    page.locator('.studio-command').wait_for()
                    menu = page.locator('.mobile-nav summary')
                    menu.tap()
                    assert page.locator('.mobile-nav').evaluate('e=>e.open')
                    menu.tap()
                    assert not page.locator('.mobile-nav').evaluate('e=>e.open')
                    results["interactions"].append({"engine":engine,"width":width,"action":"mobile drawer, ordinary touch, dock and menu", "passed":True})
                assert not console_errors, console_errors
                page.close()
            if engine == "chromium" and not baseline:
                page = browser.new_page(viewport={"width":1440,"height":1000})
                page.route("https://**/*", lambda route: route.abort())
                page.goto(url + "/?view=dashboard");ready(page)
                page.evaluate("window.uiuxDocumentSentinel='retained'")
                previous = page.locator("#uiux-runtime").get_attribute("data-runs")
                started = time.perf_counter()
                page.locator('.st-key-product_primary_routes').get_by_role("button", name="Browse jobs", exact=True).click()
                changed(page, previous)
                assert page.evaluate("window.uiuxDocumentSentinel") == "retained"
                assert "view=jobs" in page.url
                results["interactions"].append({"action":"native navigation", "seconds":round(time.perf_counter()-started,3),"document_preserved":True})

                button = page.get_by_role("button", name="Save", exact=True).first
                previous = page.locator("#uiux-runtime").get_attribute("data-runs")
                button.click();changed(page, previous)
                page.get_by_role("button", name="Saved", exact=True).first.wait_for()
                results["interactions"].append({"action":"save job", "passed":True})

                page.get_by_role("button", name="Open job details", exact=True).first.click()
                dialog = page.get_by_role("dialog")
                dialog.wait_for()
                dialog.get_by_role("button", name="Close", exact=True).first.click()
                dialog.wait_for(state="hidden")
                page.locator('.job-title').first.click()
                assert page.get_by_role("dialog").count() == 0
                results["interactions"].append({"action":"details open/dismiss and ordinary card touch", "passed":True})

                # A native filter rerun must retain the same browser document.
                search = page.get_by_role("textbox", name="Search jobs, companies, or locations", exact=True)
                previous = page.locator("#uiux-runtime").get_attribute("data-runs")
                search.fill("Compensation");search.press("Enter");changed(page, previous)
                assert page.locator('.job-title').count() > 0
                assert all("Compensation" in text for text in page.locator('.job-title').all_text_contents())
                assert page.evaluate("window.uiuxDocumentSentinel") == "retained"
                results["interactions"].append({"action":"live search", "passed":True})

                previous = page.locator("#uiux-runtime").get_attribute("data-runs")
                page.locator('.st-key-product_primary_routes').get_by_role("button", name="Dashboard", exact=True).click()
                changed(page, previous)
                panel = page.get_by_text("Application engines · MUNSHI Native & Relay", exact=True)
                panel.scroll_into_view_if_needed()
                before = page.locator("#uiux-runtime").get_attribute("data-runs")
                panel.click()
                page.get_by_text("Application engines", exact=True).wait_for()
                assert page.locator("#uiux-runtime").get_attribute("data-runs") == before
                results["interactions"].append({"action":"engine status fragment", "full_page_reruns":0})
                page.close()
            browser.close()
    (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    args = parser.parse_args()
    run(args.url.rstrip("/"), args.output, baseline=args.baseline)
