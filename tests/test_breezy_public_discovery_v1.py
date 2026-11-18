"""BreezyHR public-board discovery fixtures and production health presentation."""
import json
import socket
from pathlib import Path
import sqlite3

import pytest
import requests

from app.sources.breezyhr import fetch_breezy_board, parse_breezy_jobs
from app.source_readiness import readiness_with_boards
from app.source_ui_readiness_v1 import displayed_readiness, source_funnel

HOST = "yay-lunch.breezy.hr"
URL = f"https://{HOST}/"
BOARD = {"provider": "BreezyHR", "company_name": "Yay Lunch", "board_url": URL}


def job(**changes):
    result = {
        "id": "72a693abdc1a",
        "friendly_id": "72a693abdc1a-hr-analyst",
        "name": "HR Analyst",
        "url": "https://yay-lunch.breezy.hr/p/72a693abdc1a-hr-analyst",
        "published_date": "2026-10-19T20:38:26.778Z",
        "location": {"country":{"id":"US","name":"United States"},
                     "state":{"id":"NJ","name":"New Jersey"},
                     "city":"Newark","name":"Newark, NJ","is_remote":False},
        "locations": [{"country":{"id":"US"},"state":{"id":"PA"},
                       "city":"Philadelphia","name":"Philadelphia, PA"}],
        "type": {"id":"fullTime","name":"Full-Time"}
    }
    result.update(changes)
    return result


def test_realistic_live_json_two_locations_and_exact_job_id():
    parsed = parse_breezy_jobs([job()], BOARD)
    assert parsed["raw_count"] == 1 and len(parsed["jobs"]) == 1
    item = parsed["jobs"][0]
    assert item["ats_job_id"] == "72a693abdc1a"
    assert item["company_name"] == "Yay Lunch"
    assert item["title"] == "HR Analyst"
    assert item["country"] == "US"
    assert len(item["public_location_candidates"]) == 2
    assert item["employment_type"] == "Full-Time"


@pytest.mark.parametrize("value",[
    job(url="https://evil.example/p/72a693abdc1a-hr-analyst"),
    job(url="http://yay-lunch.breezy.hr/p/72a693abdc1a-hr-analyst"),
    job(url="https://yay-lunch.breezy.hr/redirect?to=https://evil.example"),
    job(url="https://yay-lunch.breezy.hr/p/changed"),
    job(id="not-valid!!"),
    job(name=""),
])
def test_invalid_or_cross_employer_identity_never_normalizes(value):
    result = parse_breezy_jobs([value], BOARD)
    assert result["raw_count"] == 1
    assert not result["jobs"]
    assert result["rejections"]["invalid_identity_or_url"] == 1


def test_duplicate_and_malformed_records_are_accounted():
    result = parse_breezy_jobs([job(), job(), "bad", None], BOARD)
    assert len(result["jobs"]) == 1
    assert sum(result["rejections"].values()) == 3
    assert result["raw_count"] == 4


@pytest.mark.parametrize("payload",[{},{"positions":[]},None,"[]"])
def test_payload_must_be_public_json_array(payload):
    with pytest.raises(ValueError):
        parse_breezy_jobs(payload,BOARD)


def test_fetch_board_only_allowed_host_and_two_bounded_requests(monkeypatch):
    class Resp:
        def __init__(self):self.content=b""
        def json(self):return [job()]
    class Client:
        calls=[]
        requests=2
        def __init__(self, hosts, **kwargs):
            assert hosts == {HOST} and kwargs["max_requests"] == 2
        def get(self, url):
            self.calls.append(url)
            return Resp()
    monkeypatch.setattr("app.sources.breezyhr.PublicBoardClient",Client)
    result=fetch_breezy_board(BOARD,{"request_timeout_seconds":15})
    assert result["raw_count"]==1
    assert result["requests"]==2
    assert Client.calls==[f"https://{HOST}/json"]


def test_no_unreviewed_board_is_approved(monkeypatch):
    with pytest.raises(ValueError,match="NEEDS_COMPANY_BOARD"):
        fetch_breezy_board({"provider":"BreezyHR","company_name":"Other","board_url":"https://other.breezy.hr/"},
                          {"request_timeout_seconds":15})


def test_readiness_requires_independently_verified_matching_board_contract():
    c=sqlite3.connect(":memory:")
    c.row_factory=sqlite3.Row
    c.execute("CREATE TABLE provider_board_registry(company_name TEXT, board_url TEXT,provider TEXT,enabled INTEGER)")
    source={"source_name":"BreezyHR","enabled":1,"cost_mode":"free"}
    registry={"workers":{"BreezyHR":"app.breezyhr_worker"}}
    assert readiness_with_boards(c,source,registry)["reason"]=="needs_company_board"
    c.execute("INSERT INTO provider_board_registry VALUES(?,?,?,?)",("Yay Lunch",URL,"BreezyHR",1))
    assert readiness_with_boards(c,source,registry)["ready"]
    c.execute("UPDATE provider_board_registry SET board_url='https://unverified.breezy.hr/'")
    assert readiness_with_boards(c,source,registry)["reason"]=="needs_company_board"


def test_health_ui_does_not_mark_missing_workers_as_operational():
    c=sqlite3.connect(":memory:")
    c.row_factory=sqlite3.Row
    for name in ("iCIMS","Oracle Recruiting","Rippling"):
        label,reason=displayed_readiness(c,{"source_name":name,"enabled":1,"cost_mode":"free",
                                            "health_status":"failed","last_success_at":None}, {"workers":{}})
        assert (label,reason)==("Not implemented","worker_missing")
    label,reason=displayed_readiness(c,{"source_name":"SerpAPI","enabled":1,"cost_mode":"paid"},
                                     {"workers":{}})
    assert (label,reason)==("Blocked by cost policy","disabled_by_policy")


def test_funnel_shows_real_filtering_counts_and_zero_division():
    assert source_funnel({"raw_jobs_last_run":2786,"normalized_jobs_last_run":2786,
                          "eligible_jobs_last_run":0,"inserted_jobs_last_run":0,
                          "reject_role_last_run":2743})["rejected_role"] == 2743
    assert source_funnel({"raw_jobs_last_run":40,"eligible_jobs_last_run":4})["eligible_rate_pct"] == 10
    assert source_funnel({})["eligible_rate_pct"] == 0


def test_paid_provider_not_resolved_by_public_worker():
    from app.discovery_policy import require_free_provider, DiscoveryPolicyError
    for name in ("SerpAPI","Apify"):
        with pytest.raises(DiscoveryPolicyError):
            require_free_provider(name,cost_mode="paid")
