from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from app import database, dashboard_adapter_sources_v2_3_1 as legacy_adapters
from app import employer_board_discovery
from app.provider_adapter_common import run_provider
from app.sources.bamboohr import bamboo_list_url, fetch_bamboohr_board
from app.sources.workday import fetch_workday_board, workday_api_base
from app.targeting import filter_jobs


FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeWorkdaySession:
    def __init__(self, fixture):
        self.fixture = fixture
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse(self.fixture["list"])

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse(self.fixture["detail"])


class FakeBambooSession:
    def __init__(self, fixture):
        self.fixture = fixture
        self.gets = []

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        payload = self.fixture["detail"] if url.endswith("/42/detail") else self.fixture["list"]
        return FakeResponse(payload)


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _runtime():
    return {
        "request_timeout_seconds": 5,
        "page_size": 20,
        "max_pages_per_board": 1,
        "max_jobs_per_board": 10,
        "fetch_job_details": True,
        "max_detail_requests_per_board": 10,
        "user_agent": "fixture",
    }


def test_workday_fixture_contract_and_canonical_gate(hunter_db) -> None:
    fixture = _fixture("workday_jobs.json")
    session = FakeWorkdaySession(fixture)
    result = fetch_workday_board(fixture["board"], _runtime(), session=session)
    assert workday_api_base(fixture["board"]).endswith("/wday/cxs/fixture/Careers")
    assert result["requests"] == 2
    assert result["jobs"][0]["ats_job_id"] == "REQ-100"
    assert result["jobs"][0]["country"] == "US"
    funnel = filter_jobs(result["jobs"])
    assert funnel["eligible"] == 1
    assert funnel["accounting_delta"] == 0


def test_bamboohr_fixture_contract_and_canonical_gate(hunter_db) -> None:
    fixture = _fixture("bamboohr_jobs.json")
    session = FakeBambooSession(fixture)
    result = fetch_bamboohr_board(fixture["board"], _runtime(), session=session)
    assert bamboo_list_url(fixture["board"]) == "https://fixture.bamboohr.com/careers/list"
    assert result["requests"] == 2
    assert result["jobs"][0]["ats_job_id"] == "42"
    assert result["jobs"][0]["state"] == "NY"
    funnel = filter_jobs(result["jobs"])
    assert funnel["eligible"] == 1
    assert funnel["accounting_delta"] == 0


def test_comeet_source_limit_is_global_and_fair_across_boards(
    hunter_db, monkeypatch
) -> None:
    boards = [
        {"company_name": "Alpha", "board_url": "https://example.test/alpha"},
        {"company_name": "Beta", "board_url": "https://example.test/beta"},
    ]
    monkeypatch.setattr(legacy_adapters, "load_enabled_boards", lambda _source: boards)
    monkeypatch.setattr(legacy_adapters, "_request_timeout", lambda _source: 1.0)
    monkeypatch.setattr(legacy_adapters, "_configured_source_tier", lambda _source: 1)

    def request(url, **_kwargs):
        company = "Alpha" if url.endswith("alpha") else "Beta"
        return FakeResponse(
            {
                "positions": [
                    {
                        "uid": f"{company}-{index}",
                        "name": "Software Engineer",
                        "location": "Austin, TX",
                        "description": "Build systems.",
                    }
                    for index in range(80)
                ]
            }
        )

    monkeypatch.setattr(legacy_adapters, "_request", request)
    jobs, errors = legacy_adapters.fetch_comeet(
        legacy_adapters.SOURCE_SPECS["comeet"],
        100,
    )

    assert errors == []
    assert len(jobs) == 100
    assert sum(job["company_name"] == "Alpha" for job in jobs) == 50
    assert sum(job["company_name"] == "Beta" for job in jobs) == 50


def test_pinpoint_source_limit_is_global_and_fair_across_boards(
    hunter_db, monkeypatch
) -> None:
    boards = [
        {"company_name": "Alpha", "board_url": "https://example.test/alpha"},
        {"company_name": "Beta", "board_url": "https://example.test/beta"},
    ]
    monkeypatch.setattr(legacy_adapters, "load_enabled_boards", lambda _source: boards)
    monkeypatch.setattr(legacy_adapters, "_configured_source_tier", lambda _source: 1)

    def request(url, **_kwargs):
        company = "Alpha" if url.endswith("alpha") else "Beta"
        return FakeResponse(
            [
                {
                    "id": f"{company}-{index}",
                    "title": "Software Engineer",
                    "location": "Austin, TX",
                    "description": "Build systems.",
                    "url": f"https://example.test/{company}/{index}",
                }
                for index in range(80)
            ]
        )

    monkeypatch.setattr(legacy_adapters, "_request", request)
    jobs, errors = legacy_adapters.fetch_pinpoint(
        legacy_adapters.SOURCE_SPECS["pinpoint"],
        100,
    )

    assert errors == []
    assert len(jobs) == 100
    assert sum(job["company_name"] == "Alpha" for job in jobs) == 50
    assert sum(job["company_name"] == "Beta" for job in jobs) == 50


def test_personio_source_limit_is_global_and_fair_across_boards(
    hunter_db, monkeypatch
) -> None:
    boards = [
        {"company_name": "Alpha", "board_url": "https://example.test/alpha/xml"},
        {"company_name": "Beta", "board_url": "https://example.test/beta/xml"},
    ]
    monkeypatch.setattr(legacy_adapters, "load_enabled_boards", lambda _source: boards)
    monkeypatch.setattr(legacy_adapters, "_configured_source_tier", lambda _source: 1)

    class XmlResponse:
        def __init__(self, company: str):
            positions = "".join(
                f"<position><id>{company}-{index}</id>"
                "<name>Software Engineer</name><office>Austin, TX</office>"
                "<jobDescription>Build systems.</jobDescription></position>"
                for index in range(80)
            )
            self.content = f"<positions>{positions}</positions>".encode()

    def request(url, **_kwargs):
        return XmlResponse("Alpha" if "/alpha/" in url else "Beta")

    monkeypatch.setattr(legacy_adapters, "_request", request)
    jobs, errors = legacy_adapters.fetch_personio(
        legacy_adapters.SOURCE_SPECS["personio"],
        100,
    )

    assert errors == []
    assert len(jobs) == 100
    assert sum(job["company_name"] == "Alpha" for job in jobs) == 50
    assert sum(job["company_name"] == "Beta" for job in jobs) == 50


def test_board_discovery_cannot_reenable_source_or_erase_backoff(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
                source_name,source_tier,enabled,cadence_minutes,cost_mode,
                health_status,last_run_at,last_error,consecutive_failures
            ) VALUES (
                'Personio',1,0,180,'free','failed',CURRENT_TIMESTAMP,
                'fixture timeout',2
            )
            """
        )
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_random_schedule (
                source_name TEXT PRIMARY KEY,
                next_run_at TEXT,
                base_cadence_minutes INTEGER NOT NULL,
                schedule_reason TEXT,
                schedule_state TEXT,
                consecutive_scheduler_failures INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO source_random_schedule(
                source_name,next_run_at,base_cadence_minutes,
                schedule_reason,schedule_state,consecutive_scheduler_failures
            ) VALUES (
                'Personio','2099-01-01T00:00:00+00:00',180,
                'failure_random_backoff','failure_backoff',2
            );
            """
        )
        employer_board_discovery._update_runtime_source_states(
            connection,
            {"Personio": 7},
        )
        connection.commit()
        source = connection.execute(
            "SELECT * FROM source_health WHERE source_name='Personio'"
        ).fetchone()
        schedule = connection.execute(
            "SELECT * FROM source_random_schedule WHERE source_name='Personio'"
        ).fetchone()
    finally:
        connection.close()

    assert source["enabled"] == 0
    assert source["health_status"] == "failed"
    assert source["last_error"] == "fixture timeout"
    assert source["consecutive_failures"] == 2
    assert schedule["schedule_state"] == "failure_backoff"
    assert schedule["schedule_reason"] == "failure_random_backoff"
    assert schedule["consecutive_scheduler_failures"] == 2


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("fixture provider timeout"),
        json.JSONDecodeError("fixture bad JSON", "{", 1),
        requests.HTTPError("fixture HTTP 429 rate limit"),
    ],
)
def test_provider_failures_are_retried_recorded_and_never_silent(hunter_db, failure) -> None:
    database.save_setting("orchestration", {"maintenance_mode": False}, changed_by="pytest")
    runtime_policy = json.loads(
        (FIXTURES.parent.parent / "config" / "provider_runtime_policy.json").read_text()
    )
    runtime_policy.update({"retry_attempts": 2, "retry_backoff_seconds": 0})
    database.save_setting(
        "provider_runtime",
        runtime_policy,
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode)
            VALUES ('Workday',1,1,60,'free')
            ON CONFLICT(source_name) DO UPDATE SET enabled=1
            """
        )
        connection.execute(
            """
            INSERT INTO adapter_coverage(provider,implemented,enabled,health_status)
            VALUES ('Workday',1,1,'fixture_tested')
            ON CONFLICT(provider) DO UPDATE SET implemented=1,enabled=1
            """
        )
        connection.execute(
            """
            INSERT INTO provider_board_registry(
                company_name,provider,tenant,site_name,board_url,us_relevance,enabled
            ) VALUES ('Fixture Co','Workday','fixture','Careers',
                      'https://fixture.example/careers','confirmed',1)
            """
        )
        connection.commit()
    finally:
        connection.close()

    attempts = 0

    def fail_board(_board, _runtime):
        nonlocal attempts
        attempts += 1
        raise failure

    result = run_provider("Workday", fail_board, max_boards=1)
    connection = database.get_connection()
    try:
        source = connection.execute(
            "SELECT health_status,consecutive_failures,last_error FROM source_health WHERE source_name='Workday'"
        ).fetchone()
        board = connection.execute(
            "SELECT health_status,last_job_count FROM provider_board_registry WHERE provider='Workday'"
        ).fetchone()
    finally:
        connection.close()
    assert attempts == 2
    assert result["success"] is False
    assert result["failed_boards"] == 1
    assert result["accounting_delta"] == 0
    assert type(failure).__name__ in result["errors"][0]["error"]
    assert source["health_status"] == "failed"
    assert source["consecutive_failures"] == 1
    assert source["last_error"]
    assert board["health_status"] == "failed"
    assert board["last_job_count"] == 0
