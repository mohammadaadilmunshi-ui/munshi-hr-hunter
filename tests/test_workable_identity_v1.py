"""Read-only Workable alias/dedup regression for the OneTouch incident."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import workable_identity_v1 as identity
from app import relay_application_dispatch_v1 as relay

SOURCE = "https://apply.workable.com/j/5D934446BE"
REDIRECT = "https://apply.workable.com/otdcareers/j/5D934446BE"
CANONICAL = "https://apply.workable.com/otdcareers/j/FB866606FC"
EMPLOYER = "https://otdcareers.workable.com/jobs/6274373"
PAGE = (
    '<html><head><meta name="job" content="6274373">'
    f'<link rel="canonical" href="{CANONICAL}"></head><body></body></html>'
)


class FakeHttpClient:
    def __init__(self, redirect=REDIRECT, body=PAGE):
        self.redirect = redirect
        self.body = body
        self.visits = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, url, **_kwargs):
        self.visits.append(url)
        if len(self.visits) == 1:
            return SimpleNamespace(status_code=302, headers={"location": self.redirect})
        return SimpleNamespace(
            status_code=200, headers={},
            content=self.body.encode(), text=self.body,
        )


class ReadOnlyProvider:
    def __init__(self, existing=True):
        self.existing = existing
        self.read_calls = []
        self.application_calls = 0

    def call_read(self, name, args):
        self.read_calls.append((name, dict(args)))
        if name == "list_applications":
            matches = (
                [{"url": EMPLOYER, "applicationId": "prior-workable-123", "status": "COMPLETED"}]
                if self.existing and args["query"] == "6274373"
                else []
            )
            return {"structuredContent": {"result": {"applications": matches}}}
        if name == "get_application_status":
            return {"structuredContent": {"result": {
                "status": "COMPLETED", "statusLabel": "sent",
                "isTerminal": True, "needsAction": False, "errorCode": None,
            }}}
        raise AssertionError("Only read-only provider tools are allowed")

    def call_application(self, *_args, **_kwargs):
        self.application_calls += 1
        raise AssertionError("No outbound application is allowed in this test")


def test_public_workable_aliases_include_numeric_and_canonical_id(monkeypatch):
    identity.resolved_workable_identities.cache_clear()
    fake = FakeHttpClient()
    monkeypatch.setattr(identity.httpx, "Client", lambda **_kwargs: fake)
    resolved = identity.resolved_workable_identities(SOURCE)
    assert "workable:code:5d934446be" in resolved
    assert "workable:code:fb866606fc" in resolved
    assert "workable:otdcareers:job:6274373" in resolved
    assert fake.visits == [SOURCE, REDIRECT]
    assert len(identity.lookup_terms(SOURCE, resolved)) >= 2


def test_prior_provider_submission_detects_custom_workable_url(monkeypatch):
    prior_ids = frozenset({
        "workable:code:5d934446be",
        "workable:code:fb866606fc",
        "workable:otdcareers:job:6274373",
    })
    monkeypatch.setattr(relay, "resolved_workable_identities", lambda _: prior_ids)
    provider = ReadOnlyProvider(existing=True)
    assert relay._prior_provider_application(provider, SOURCE) is True
    assert relay._confirmed_application(provider, SOURCE) == ("prior-workable-123", "COMPLETED")
    assert provider.application_calls == 0
    assert any(args["query"] == "6274373" for name, args in provider.read_calls)


def test_prior_provider_absence_is_not_synthetic_success(monkeypatch):
    monkeypatch.setattr(
        relay, "resolved_workable_identities",
        lambda _: frozenset({"workable:code:5d934446be"}),
    )
    provider = ReadOnlyProvider(existing=False)
    assert relay._prior_provider_application(provider, SOURCE) is False
    assert provider.application_calls == 0


def test_workable_redirect_outside_trusted_domain_fails_closed(monkeypatch):
    identity.resolved_workable_identities.cache_clear()
    fake = FakeHttpClient(redirect="http://127.0.0.1/private")
    monkeypatch.setattr(identity.httpx, "Client", lambda **_kwargs: fake)
    with pytest.raises(RuntimeError, match="untrusted redirect"):
        identity.resolved_workable_identities(SOURCE)
    assert fake.visits == [SOURCE]


def test_workable_missing_canonical_evidence_fails_closed(monkeypatch):
    identity.resolved_workable_identities.cache_clear()
    fake = FakeHttpClient(body="<html><head></head><body>No job identity</body></html>")
    monkeypatch.setattr(identity.httpx, "Client", lambda **_kwargs: fake)
    with pytest.raises(RuntimeError, match="alias evidence"):
        identity.resolved_workable_identities(SOURCE)



def test_numeric_workable_job_identity_supports_both_alias_shapes():
    urls = (
        "https://apply.workable.com/otdcareers/jobs/6274373",
        "https://otdcareers.workable.com/jobs/6274373",
    )
    for url in urls:
        assert "workable:otdcareers:job:6274373" in identity.url_identities(url)
