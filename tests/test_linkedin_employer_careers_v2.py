from __future__ import annotations

import json

import pytest

from app.linkedin_employer_careers_v2 import (
    board_binding_from_ats_company_registry,
    board_hints_from_official_careers_html,
    registered_company_board_coverage,
    resolve_from_known_company_boards,
)


def healthy_board(**overrides):
    return {
        "company_name": "Example Co",
        "ats_type": "ashby",
        "board_token": "example",
        "careers_url": "https://jobs.ashbyhq.com/example",
        "enabled": 1,
        "health_status": "healthy",
        **overrides,
    }


def linkedin(**overrides):
    return {
        "company_name": "Example Co",
        "title": "HR Analyst",
        "location_raw": "New York, NY",
        "description_raw": "Our recruiting analysis supports compensation benchmarking for employees. "
        "Collaborate with HR partners on dashboards and workforce analytics. "
        "You will evaluate salary trends, workforce metrics and reporting tools.",
        "job_url": "https://www.linkedin.com/jobs/view/4455550001",
        "ats_job_id": "4455550001",
        "apply_url": None,
        **overrides,
    }


def test_healthy_registry_binding_identifies_board():
    result = board_binding_from_ats_company_registry(healthy_board())
    assert result is not None
    assert (result.provider, result.board_name, result.confidence) == (
        "Ashby",
        "example",
        "registry_healthy",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": 0},
        {"health_status": "unhealthy"},
        {"health_status": "not_configured"},
        {"board_token": "../root"},
        {"careers_url": "https://jobs.ashbyhq.com/different"},
        {"careers_url": "https://jobs.ashbyhq.com.evil.com/example"},
        {"careers_url": "http://jobs.ashbyhq.com/example"},
        {"careers_url": "https://foo:bar@jobs.ashbyhq.com/example"},
        {"careers_url": "https://192.168.0.1/example"},
        {"ats_type": "unknown"},
    ],
)
def test_registry_fails_closed_without_verified_existing_provenance(change):
    assert board_binding_from_ats_company_registry(healthy_board(**change)) is None


def test_official_careers_html_extracts_known_ats_boards():
    html = """
    <html><body>
      <a href='https://jobs.ashbyhq.com/example/1234'>Engineering</a>
      <a href='https://jobs.ashbyhq.com/example/5678'>People</a>
      <iframe src='https://jobs.lever.co/example'></iframe>
      <a href='https://jobs.ashbyhq.com.evil.com/example'>Unknown</a>
      <a href='https://jobs.smartrecruiters.com/example/Careers'>Marketing</a>
    </body></html>
    """
    hints = board_hints_from_official_careers_html(
        html,
        verified_careers_url="https://careers.example.com/opportunities",
        verified_employer_host="example.com",
    )
    assert {(h.provider, h.board_name) for h in hints} == {
        ("Ashby", "example"),
        ("Lever", "example"),
        ("SmartRecruiters", "example"),
    }
    assert all(h.confidence == "hint" for h in hints)


def test_unverified_employer_domain_rejected():
    with pytest.raises(ValueError):
        board_hints_from_official_careers_html(
            "<a href='https://jobs.ashbyhq.com/example'>x</a>",
            verified_careers_url="https://careers.attacker.com",
            verified_employer_host="example.com",
        )


def test_board_hints_are_not_application_verification():
    hints = board_hints_from_official_careers_html(
        "<a href='https://boards.greenhouse.io/example/jobs/321'>Apply</a>",
        verified_careers_url="https://www.example.com/careers",
        verified_employer_host="example.com",
    )
    assert hints[0].confidence == "hint"


def test_no_company_registry_match():
    result = resolve_from_known_company_boards(
        linkedin(), [healthy_board(company_name="Different Company")]
    )
    assert result.resolution_status == "UNKNOWN"
    assert result.employer_apply_url is None


def test_known_board_does_not_auto_call_network():
    class ExplodingOpener:
        def open(self, *args, **kwargs):
            raise AssertionError("Network was attempted unexpectedly")

    result = resolve_from_known_company_boards(
        linkedin(), [healthy_board()], provider_opener=ExplodingOpener()
    )
    assert result.resolution_status == "UNKNOWN"
    assert result.evidence["reason"] == "board_known_but_not_queried"


def test_ambiguous_known_boards_not_queried():
    result = resolve_from_known_company_boards(
        linkedin(),
        [
            healthy_board(),
            healthy_board(
                board_token="second", careers_url="https://jobs.ashbyhq.com/second"
            ),
        ],
        allow_network=True,
    )
    assert result.resolution_status == "AMBIGUOUS"
    assert result.evidence["board_count"] == 2


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.payload))}

    def read(self, limit):
        assert limit >= len(self.payload)
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        assert request.get_method() == "GET"
        assert timeout == 7
        return FakeResponse(self.payload)


def test_known_board_reads_official_feed_but_not_submit():
    opener = FakeOpener(
        {
            "jobs": [
                {
                    "title": "HR Analyst",
                    "location": "New York, NY",
                    "descriptionPlain": linkedin()["description_raw"],
                    "applyUrl": "https://jobs.ashbyhq.com/example/ABC/apply",
                    "jobUrl": "https://jobs.ashbyhq.com/example/ABC",
                    "isListed": True,
                }
            ]
        }
    )
    result = resolve_from_known_company_boards(
        linkedin(),
        [healthy_board()],
        allow_network=True,
        provider_opener=opener,
    )
    assert result.resolution_status == "VERIFIED"
    assert result.employer_apply_url.endswith("/ABC/apply")
    assert opener.urls == ["https://api.ashbyhq.com/posting-api/job-board/example"]


def test_company_board_coverage_offline():
    coverage = registered_company_board_coverage(
        [linkedin(), linkedin(company_name="Other Co")],
        [
            healthy_board(),
            healthy_board(company_name="Other Co", health_status="unhealthy"),
        ],
    )
    assert coverage["total_linkedin"] == 2
    assert coverage["linked_to_one_healthy_known_board"] == 1
    assert coverage["board_unknown_or_not_healthy"] == 1
    assert coverage["public_api_requests"] == 0


class TwoStepGreenhouseOpener:
    def __init__(self, *, wrong_identity=False):
        self.urls: list[str] = []
        self.wrong_identity = wrong_identity

    def open(self, request, timeout):
        assert request.get_method() == "GET"
        assert timeout == 7
        url = request.full_url
        self.urls.append(url)
        if url.endswith("/jobs"):
            payload = {
                "jobs": [
                    {
                        "id": 112233,
                        "title": "HR Analyst",
                        "location": {"name": "New York, NY"},
                        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/112233",
                    }
                ]
            }
        elif url.endswith("/112233?content=true"):
            payload = {
                "id": 999 if self.wrong_identity else 112233,
                "content": "<p>" + linkedin()["description_raw"] + "</p>",
            }
        else:
            raise AssertionError(f"Unexpected GET endpoint: {url}")
        return FakeResponse(payload)


def test_greenhouse_job_detail_raises_when_posting_identity_differs():
    from app.linkedin_public_ats_feed_v1 import fetch_greenhouse_job_detail

    with pytest.raises(ValueError, match="wrong posting"):
        fetch_greenhouse_job_detail(
            "example", 112233, opener=TwoStepGreenhouseOpener(wrong_identity=True)
        )


@pytest.mark.parametrize("bad", ["../admin", "1?redirect=http://localhost", 0, None])
def test_greenhouse_detail_id_rejects_untrusted_values(bad):
    from app.linkedin_public_ats_feed_v1 import greenhouse_detail_url

    with pytest.raises(ValueError):
        greenhouse_detail_url("example", bad)


def test_greenhouse_verification_uses_bounded_list_then_exact_detail_get():
    opener = TwoStepGreenhouseOpener()
    result = resolve_from_known_company_boards(
        linkedin(),
        [
            healthy_board(
                ats_type="greenhouse",
                board_token="example",
                careers_url="https://job-boards.greenhouse.io/example",
            )
        ],
        allow_network=True,
        provider_opener=opener,
    )
    assert result.resolution_status == "VERIFIED"
    assert result.employer_apply_url.endswith("/112233")
    assert len(opener.urls) == 2
    assert all(
        u.startswith("https://boards-api.greenhouse.io/v1/boards/example/")
        for u in opener.urls
    )


def test_greenhouse_wrong_detail_cannot_be_verified():
    opener = TwoStepGreenhouseOpener(wrong_identity=True)
    result = resolve_from_known_company_boards(
        linkedin(),
        [
            healthy_board(
                ats_type="greenhouse",
                board_token="example",
                careers_url="https://job-boards.greenhouse.io/example",
            )
        ],
        allow_network=True,
        provider_opener=opener,
    )
    assert result.resolution_status == "CANDIDATE"


def test_provider_timeout_fails_closed_as_unknown():
    class TimeoutOpener:
        def open(self, *_args, **_kwargs):
            raise TimeoutError("Read deadline reached")

    result = resolve_from_known_company_boards(
        linkedin(),
        [healthy_board()],
        allow_network=True,
        provider_opener=TimeoutOpener(),
    )
    assert result.resolution_status == "UNKNOWN"
    assert result.employer_apply_url is None
    assert result.evidence["reason"] == "public_board_read_failed"


def test_registry_audit_is_readonly(tmp_path):
    import sqlite3

    from scripts.linkedin_careers_registry_audit_v2 import audit_registry
    from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection

    path = tmp_path / "sample.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs(company_name TEXT,source TEXT)")
        connection.execute(
            "CREATE TABLE ats_company_registry("
            "company_name TEXT,ats_type TEXT,board_token TEXT,"
            "careers_url TEXT,enabled INTEGER,health_status TEXT)"
        )
        connection.execute("INSERT INTO jobs VALUES('Example Co','JobSpy/linkedin')")
        connection.execute(
            "INSERT INTO ats_company_registry VALUES "
            "('Example Co','ashby','example',"
            "'https://jobs.ashbyhq.com/example',1,'healthy')"
        )
    before = path.read_bytes()
    with read_only_connection(path) as connection:
        result = audit_registry(connection)
        assert result["linked_to_one_healthy_known_board"] == 1
        assert result["network_requests"] == 0
        assert result["linked_jobs_submitted"] == 0
    assert path.read_bytes() == before
