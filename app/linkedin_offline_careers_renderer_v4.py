"""Offline-only JavaScript rendering for previously approved public HTML snapshots.

All browser network requests are aborted. This is a diagnostic component, NOT
a complete security sandbox or safe production crawler. Production needs an
OS/network-egress isolation boundary before rendering untrusted live pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.linkedin_first_party_careers_v3 import (
    MAX_HTML_BYTES,
    ApprovedEmployerSite,
    CareerSiteResult,
    discover_from_approved_html,
    validate_approved_site,
)

JS_GUARD = """
(() => {
  window.open = () => null;
  window.fetch = () => Promise.reject(new Error("OFFLINE_RENDER"));
  window.WebSocket = class {
    constructor() { throw new Error("OFFLINE_RENDER"); }
  };
  window.EventSource = class {
    constructor() { throw new Error("OFFLINE_RENDER"); }
  };
  if (navigator.sendBeacon) {
    navigator.sendBeacon = () => false;
  }
})();
"""


@dataclass(frozen=True)
class OfflineRenderOutcome:
    result: CareerSiteResult
    blocked_requests: int
    elapsed_ms: int
    status: str


def render_approved_snapshot_offline(
    site: ApprovedEmployerSite,
    html: str,
    *,
    enable_browser: bool = False,
    chromium: Any = None,
    timeout_ms: int = 5000,
) -> OfflineRenderOutcome:
    """Render local HTML without network access; extract only board hints.

    Browser launch is disabled unless caller opts in; injected chromium object
    supports fully synthetic, no-network unit tests. Browser runs without any
    candidate profile, cookies, local storage, or logged-in session.
    """
    validate_approved_site(site)
    if not isinstance(html, str) or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise ValueError("Offline HTML snapshot is missing or too large")
    if timeout_ms < 200 or timeout_ms > 10000:
        raise ValueError("Browser render timeout outside bounded range")
    if not enable_browser:
        raise ValueError("Offline JS browser renderer is disabled by default")

    def _render(browser_type: Any) -> tuple[str, int]:
        blocked = 0
        browser = browser_type.launch(
            headless=True,
            args=[
                "--disable-background-networking",
                "--disable-extensions",
                "--disable-sync",
            ],
        )
        try:
            context = browser.new_context(
                service_workers="block",
                java_script_enabled=True,
                accept_downloads=False,
                ignore_https_errors=False,
            )
            try:

                def reject(route: Any) -> None:
                    nonlocal blocked
                    blocked += 1
                    route.abort()

                context.route("**/*", reject)
                if hasattr(context, "route_web_socket"):
                    context.route_web_socket("**/*", lambda ws: ws.close())
                context.add_init_script(JS_GUARD)
                page = context.new_page()
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.on("popup", lambda popup: popup.close())
                page.set_default_timeout(timeout_ms)
                page.set_content(
                    html, wait_until="domcontentloaded", timeout=timeout_ms
                )
                # Bound client-side inline scripts, but never wait for external loads.
                page.wait_for_timeout(min(timeout_ms // 5, 700))
                output = page.content()
                if len(output.encode("utf-8")) > MAX_HTML_BYTES:
                    raise ValueError("Rendered HTML exceeds bounded inspection size")
                return output, blocked
            finally:
                context.close()
        finally:
            browser.close()

    if chromium is None:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            rendered, blocked = _render(playwright.chromium)
    else:
        rendered, blocked = _render(chromium)

    result = discover_from_approved_html(site, rendered)
    return OfflineRenderOutcome(
        result=result,
        blocked_requests=blocked,
        elapsed_ms=0,  # no fabricated timing measurement
        status="OFFLINE_HINTS_ONLY",
    )
