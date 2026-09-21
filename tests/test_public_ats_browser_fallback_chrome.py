from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("MUNSHI_RUN_BROWSER_TESTS") != "1",
    reason="real Chromium integration lane is opt-in",
)

from app.sources.public_ats import _render_page, inspect_public_page  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"""<!doctype html>
<html>
<head><title>ATS fixture</title></head>
<body>
<div id="root">Loading jobs</div>
<script>
setTimeout(() => {
  const node = document.createElement('script');
  node.type = 'application/ld+json';
  node.textContent = JSON.stringify({
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "People Analytics Analyst",
    "hiringOrganization": {"name": "Acme"},
    "identifier": {"value": "browser-fixture-1"},
    "url": window.location.href + "jobs/123"
  });
  document.head.appendChild(node);
}, 100);
</script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def test_real_chromium_renders_dynamic_jobposting() -> None:
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        rendered = _render_page(
            url,
            {
                "request_timeout_seconds": 10,
                "public_ats_browser_fallback_enabled": True,
                "public_ats_render_wait_ms": 500,
                "user_agent": "MUNSHI fixture",
            },
        )
        assert rendered is not None
        inspected = inspect_public_page("Teamtailor", url, rendered)
        assert inspected["blocked"] is False
        assert inspected["structured_jobs"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
