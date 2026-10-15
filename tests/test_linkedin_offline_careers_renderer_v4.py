from __future__ import annotations

import pytest

from app.linkedin_first_party_careers_v3 import ApprovedEmployerSite
from app.linkedin_offline_careers_renderer_v4 import render_approved_snapshot_offline


def site():
    return ApprovedEmployerSite(
        company_name="Example Co",
        official_domain="example.com",
        careers_url="https://www.example.com/careers/",
        approval_source="signed_registry",
        approval_reference="verified-test-fixture",
    )


def test_browser_disabled_by_default():
    with pytest.raises(ValueError, match="disabled"):
        render_approved_snapshot_offline(
            site(), "<script>document.body.textContent='hello'</script>"
        )


def test_timeout_is_bounded():
    with pytest.raises(ValueError, match="timeout"):
        render_approved_snapshot_offline(
            site(), "<html></html>", enable_browser=True, timeout_ms=60
        )


def test_browser_can_render_inline_job_board_without_outbound_network():
    html = """<!doctype html><html><body><script>
    const link = document.createElement('a');
    link.href = 'https://jobs.ashbyhq.com/example/12345';
    link.textContent = 'Jobs';
    document.body.appendChild(link);
    </script>
    <img src="https://tracking.example.org/collect?secret=none">
    </body></html>"""
    outcome = render_approved_snapshot_offline(
        site(), html, enable_browser=True, timeout_ms=5000
    )
    assert outcome.status == "OFFLINE_HINTS_ONLY"
    assert outcome.result.evidence_status == "HINTS_ONLY"
    assert {(b.provider, b.board_name) for b in outcome.result.board_hints} == {
        ("Ashby", "example")
    }
    assert outcome.blocked_requests >= 1
    assert outcome.result.evidence["application_url_verified"] is False


def test_remote_external_javascript_cannot_load():
    html = """<html><body>
    <script src="https://untrusted.example.org/app.js"></script>
    <div id="root">No links</div></body></html>"""
    outcome = render_approved_snapshot_offline(
        site(), html, enable_browser=True, timeout_ms=5000
    )
    assert outcome.result.board_hints == []
    assert outcome.blocked_requests >= 1


def test_unapproved_first_party_location_rejected_before_browser():
    from dataclasses import replace

    with pytest.raises(ValueError):
        render_approved_snapshot_offline(
            replace(site(), careers_url="https://careers.example.com.evil.com/jobs"),
            "<html></html>",
            enable_browser=True,
        )
