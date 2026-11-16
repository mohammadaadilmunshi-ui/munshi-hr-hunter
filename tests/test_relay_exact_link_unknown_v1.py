"""Relay lost-callback reconciliation from exact job links, not fuzzy titles.

Every provider and DB interaction is simulated; no applications, tokens,
emails or production databases are touched.
"""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from app import relay_application_dispatch_v1 as relay

ONE_TOUCH_URL = "https://apply.workable.com/j/5D934446BE"
ANSA_URL = "https://job-boards.greenhouse.io/ansabiotechnologies/jobs/4753487005"
CLAIMED_AT = "2026-11-16T20:22:41+00:00"
CREATED_AT = "2026-11-16T20:22:43.755Z"


class Provider:
    def __init__(self, url, rows):
        self.url = url
        self.rows = rows
        self.read_calls = []
        self.apply_calls = []

    def call_read(self, tool, args):
        self.read_calls.append((tool, dict(args)))
        if tool == "list_applications":
            rows = self.rows if args["query"] == self.url else []
            return {"structuredContent": {"result": {"applications": rows}}}
        if tool == "get_application_status":
            return {"structuredContent": {"result": {
                "applicationId": args["applicationId"],
                "status": "COMPLETED", "statusLabel": "sent",
                "isTerminal": True, "needsAction": False, "errorCode": None,
            }}}
        raise AssertionError("Only bounded read tools are permitted")

    def call_application(self, *args, **kwargs):
        self.apply_calls.append((args, kwargs))
        raise AssertionError("A test must never submit to any employer")


def _record(*, ident="provider-outcome-1", when=CREATED_AT, matched_on=None, url=None):
    return {
        "applicationId": ident,
        "status": "COMPLETED",
        "statusLabel": "sent",
        "createdAt": when,
        "matchedOn": ["job link"] if matched_on is None else matched_on,
        "url": url,
    }


def test_url_less_exact_job_link_blocks_duplicate_before_submission(monkeypatch):
    monkeypatch.setattr(
        relay, "resolved_workable_identities",
        lambda _: frozenset({"workable:code:5d934446be"}),
    )
    provider = Provider(ONE_TOUCH_URL, [_record()])
    assert relay._prior_provider_application(provider, ONE_TOUCH_URL) is True
    assert relay._confirmed_application(provider, ONE_TOUCH_URL) == (
        "provider-outcome-1", "COMPLETED"
    )
    assert provider.apply_calls == []
    assert provider.read_calls[0][1]["query"] == ONE_TOUCH_URL


@pytest.mark.parametrize("bad_row", [
    _record(matched_on=["company"]),
    _record(matched_on=["job title"]),
    _record(matched_on="job link"),
    _record(url="https://apply.workable.com/other/j/AAAAAAAAAA"),
    _record(ident=""),
])
def test_fuzzy_or_contradictory_provider_match_never_counts(monkeypatch, bad_row):
    monkeypatch.setattr(
        relay, "resolved_workable_identities",
        lambda _: frozenset({"workable:code:5d934446be"}),
    )
    provider = Provider(ONE_TOUCH_URL, [bad_row])
    assert relay._prior_provider_application(provider, ONE_TOUCH_URL) is False
    assert provider.apply_calls == []


def test_greenhouse_exact_url_without_returned_url_matches_correct_job():
    provider = Provider(ANSA_URL, [_record()])
    assert relay._prior_provider_application(provider, ANSA_URL) is True
    assert relay._confirmed_application(provider, ANSA_URL) is not None
    assert provider.apply_calls == []


def _route_and_claim():
    url_sha = hashlib.sha256(ONE_TOUCH_URL.encode()).hexdigest()
    route = {
        "route_id": "route-1",
        "application_id": "app-1",
        "job_id": 8518,
        "provider": "TSENTA",
        "state": "PREPARATION",
        "employer_url": ONE_TOUCH_URL,
        "employer_url_sha256": url_sha,
        "preflight_package_id": "preflight-1",
    }
    claim = {
        "claim_key": "one-use-1",
        "route_id": "route-1",
        "application_id": "app-1",
        "job_id": 8518,
        "state": "UNKNOWN",
        "pdf_sha256": "e" * 64,
        "employer_url_sha256": url_sha,
        "created_at": CLAIMED_AT,
    }
    return route, claim


def test_unknown_claim_can_be_recovered_only_when_every_binding_is_proven():
    route, claim = _route_and_claim()
    assert relay._matches_unknown_claim(
        _record(), route, claim, pdf_artifact_bound=True
    )
    for changed in (
        {"route_id": "different"},
        {"job_id": 8486},
        {"application_id": "different"},
        {"employer_url_sha256": "f" * 64},
        {"state": "CLAIMED"},
        {"created_at": ""},
    ):
        assert not relay._matches_unknown_claim(
            _record(), route, claim | changed, pdf_artifact_bound=True
        )
    assert not relay._matches_unknown_claim(
        _record(), route, claim, pdf_artifact_bound=False
    )


@pytest.mark.parametrize("provider_created", [
    "2026-11-16T16:06:35Z",   # Earlier manual/Workable receipt
    "2026-11-16T20:53:01Z",   # Outside bounded 30-minute claim window
    "",
])
def test_old_future_or_missing_provider_time_cannot_prove_later_claim(provider_created):
    route, claim = _route_and_claim()
    assert not relay._matches_unknown_claim(
        _record(when=provider_created), route, claim, pdf_artifact_bound=True
    )


def test_unknown_route_reconciliation_never_issues_second_application(monkeypatch):
    from app import relay_inbox_receipt_v1 as inbox

    route, claim = _route_and_claim()
    log = []

    class DB:
        def execute(self, sql, params=()):
            if "SELECT * FROM application_provider_routes_v1" in sql:
                result = route
            elif "sqlite_master" in sql:
                result = (1,)
            elif "SELECT * FROM tsenta_external_dispatch_claims_v1" in sql:
                result = claim
            elif "SELECT 1 FROM application_preflight_packages" in sql:
                result = (1,)
            elif "SELECT already_applied FROM jobs" in sql:
                result = {"already_applied": 1}
            else:
                raise AssertionError("Unexpected SQL: " + sql[:120])
            return SimpleNamespace(fetchone=lambda: result)

        def close(self):
            pass

    provider = Provider(ONE_TOUCH_URL, [_record()])

    class Client:
        def __init__(self, token):
            assert token == "synthetic-token"

        def __enter__(self):
            return provider

        def __exit__(self, *args):
            return None

    provider.initialize = lambda: None
    monkeypatch.setattr(relay, "get_connection", DB)
    monkeypatch.setattr(
        relay, "current_owner",
        lambda _: SimpleNamespace(tenant_id="tenant", user_id="user"),
    )
    monkeypatch.setattr(relay, "TsentaMcpApplicationClient", Client)
    monkeypatch.setattr(relay, "TsentaMcpReadClient", Client)
    monkeypatch.setattr(relay, "_token_for_current_owner", lambda: "synthetic-token")
    monkeypatch.setattr(
        relay, "resolved_workable_identities",
        lambda _: frozenset({"workable:code:5d934446be"}),
    )
    monkeypatch.setattr(
        relay.router, "record_provider_status",
        lambda **kw: log.append(("provider_status", kw)) or {"state": "PROVIDER_SUBMITTED"},
    )
    monkeypatch.setattr(
        relay, "_mark_claim",
        lambda key, state, ident=None: log.append(("claim", state, ident)),
    )
    monkeypatch.setattr(
        inbox, "reconcile_employer_inbox",
        lambda **kw: {"status": "NO_CORRELATED_EMPLOYER_EMAIL"},
    )
    result = relay.reconcile_from_dashboard(8518)
    assert result == {
        "status": "PROVIDER_SUBMITTED",
        "employer_receipt_verified": False,
        "retry_allowed": False,
    }
    assert [name for name, *_ in log] == ["provider_status", "claim"]
    assert log[0][1]["provider_application_id"] == "provider-outcome-1"
    assert log[1][1:] == ("ACCEPTED", "provider-outcome-1")
    assert provider.apply_calls == []
