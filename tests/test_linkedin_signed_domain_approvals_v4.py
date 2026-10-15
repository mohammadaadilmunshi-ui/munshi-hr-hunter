from __future__ import annotations

import base64
from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.linkedin_signed_domain_approvals_v4 import (
    canonical_approval_bytes,
    resolve_from_signed_approval,
    verify_signed_employer_approval,
)

NOW = datetime(2026, 10, 15, 17, 0, tzinfo=timezone.utc)


def valid_signed():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    fields = {
        "company_name": "Example Co",
        "official_domain": "example.com",
        "careers_url": "https://careers.example.com/jobs",
        "evidence_url": "https://www.example.com/about",
        "reviewer_id": "operator-review-123",
        "reviewed_at": "2026-10-01T00:00:00Z",
        "expires_at": "2026-12-01T00:00:00Z",
    }
    signature = base64.urlsafe_b64encode(
        private.sign(canonical_approval_bytes(fields))
    ).decode("ascii")
    return {
        "schema": "munshi-linkedin-employer-domain-approval-v1",
        "key_id": "operator-v1",
        "site": fields,
        "signature": signature,
    }, {"operator-v1": public}


def test_signed_operator_approval_authorizes_exact_careers_domain():
    record, trust = valid_signed()
    result = verify_signed_employer_approval(record, trust, at=NOW)
    assert result.company_name == "Example Co"
    assert result.official_domain == "example.com"
    assert result.careers_url == "https://careers.example.com/jobs"
    assert result.approval_source == "signed_registry"
    assert len(result.approval_reference) == 64


@pytest.mark.parametrize(
    "changed",
    [
        ("company_name", "Attacker, Inc"),
        ("official_domain", "evil.example"),
        ("careers_url", "https://careers.evil.example"),
        ("evidence_url", "https://attacker.example.com/about"),
        ("reviewer_id", "different"),
        ("expires_at", "2027-01-15T00:00:00Z"),
    ],
)
def test_tampered_fields_cannot_pass_signature(changed):
    record, trust = valid_signed()
    field, value = changed
    record["site"][field] = value
    with pytest.raises(ValueError):
        verify_signed_employer_approval(record, trust, at=NOW)


def test_approval_expires_and_can_be_revoked():
    record, trust = valid_signed()
    result = verify_signed_employer_approval(record, trust, at=NOW)
    with pytest.raises(ValueError, match="expired"):
        verify_signed_employer_approval(
            record, trust, at=datetime(2026, 12, 5, tzinfo=timezone.utc)
        )
    with pytest.raises(ValueError, match="revoked"):
        verify_signed_employer_approval(
            record,
            trust,
            at=NOW,
            revoked_approval_ids=frozenset({result.approval_reference}),
        )


def test_unsigned_text_and_unknown_key_cannot_authorize():
    record, trust = valid_signed()
    with pytest.raises(ValueError, match="Unknown"):
        verify_signed_employer_approval({**record, "key_id": "attacker"}, trust, at=NOW)
    with pytest.raises(ValueError):
        verify_signed_employer_approval({**record, "signature": "fake"}, trust, at=NOW)


def test_signed_gateway_is_offline_by_default_and_submit_never_authorized():
    record, trust = valid_signed()
    job = {
        "company_name": "Example Co",
        "title": "HR Analyst",
        "job_url": "https://www.linkedin.com/jobs/view/4455550011",
    }
    result = resolve_from_signed_approval(job, record, trust, at=NOW)
    assert result.resolution_status == "UNKNOWN"
    assert result.employer_apply_url is None
    assert result.evidence["reason"] == "network_opt_in_required"


def test_offline_signature_invalid_before_any_fetch():
    record, trust = valid_signed()
    record["site"]["company_name"] = "Changed"
    called = []

    def fake_fetch(*args):
        called.append(args)
        raise AssertionError("Network must not run")

    with pytest.raises(ValueError):
        resolve_from_signed_approval(
            {"company_name": "Example Co"},
            record,
            trust,
            at=NOW,
            allow_network=True,
            approved_site_fetcher=fake_fetch,
        )
    assert not called


def test_unsigned_unknown_attributes_are_rejected():
    record, trust = valid_signed()
    record["site"]["is_admin"] = "yes"
    with pytest.raises(ValueError):
        verify_signed_employer_approval(record, trust, at=NOW)


def test_signed_gateway_uses_offline_renderer_only_after_static_has_no_board():
    from app.linkedin_employer_careers_v2 import BoardHint
    from app.linkedin_first_party_careers_v3 import CareerSiteResult
    from app.linkedin_offline_careers_renderer_v4 import OfflineRenderOutcome

    record, trust = valid_signed()
    html = "<html><script>document.body.innerHTML='no static boards'</script></html>"
    expected_description = (
        "Build people dashboards and track employee compensation trends. "
        "Partner with payroll and recruiting to improve HR analytics reporting. "
        "Review benefits strategy, headcount forecasting and performance data."
    )

    class MockResponse:
        status = 200

        def __init__(self, value):
            import json

            self.payload = json.dumps(value).encode()
            self.headers = {"Content-Length": str(len(self.payload))}

        def read(self, n):
            return self.payload[:n]

        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return None

    class OfficialATS:
        def __init__(self):
            self.requests = []

        def open(self, request, timeout):
            self.requests.append(request.full_url)
            assert request.get_method() == "GET"
            assert timeout == 7
            if request.full_url.endswith("/jobs"):
                return MockResponse(
                    {
                        "jobs": [
                            {
                                "id": 321,
                                "title": "HR Analyst",
                                "location": {"name": "New York, NY"},
                                "absolute_url": "https://job-boards.greenhouse.io/example/jobs/321",
                            }
                        ]
                    }
                )
            if request.full_url.endswith("/jobs/321?content=true"):
                return MockResponse({"id": 321, "content": expected_description})
            raise AssertionError("Undocumented ATS URL")

    calls = []

    def fake_render(site, snapshot, *, enable_browser):
        calls.append((site.official_domain, snapshot, enable_browser))
        return OfflineRenderOutcome(
            result=CareerSiteResult(
                company_name="Example Co",
                careers_url=site.careers_url,
                board_hints=[
                    BoardHint(
                        "Greenhouse",
                        "example",
                        "https://job-boards.greenhouse.io/example",
                        "hint",
                    )
                ],
                evidence_status="HINTS_ONLY",
                evidence={"application_url_verified": False},
            ),
            blocked_requests=1,
            elapsed_ms=0,
            status="OFFLINE_HINTS_ONLY",
        )

    ats = OfficialATS()
    result = resolve_from_signed_approval(
        {
            "company_name": "Example Co",
            "title": "HR Analyst",
            "job_url": "https://www.linkedin.com/jobs/view/4455550011",
            "location_raw": "New York, NY",
            "description_raw": expected_description,
        },
        record,
        trust,
        at=NOW,
        allow_network=True,
        approved_site_fetcher=lambda _: html,
        allow_offline_js=True,
        offline_js_renderer=fake_render,
        ats_opener=ats,
    )
    assert result.resolution_status == "VERIFIED"
    assert result.evidence["board_hint_method"] == "offline_javascript"
    assert result.evidence["submit_authorized"] is False
    assert (
        result.employer_apply_url == "https://job-boards.greenhouse.io/example/jobs/321"
    )
    assert calls == [("example.com", html, True)]
    assert len(ats.requests) == 2
