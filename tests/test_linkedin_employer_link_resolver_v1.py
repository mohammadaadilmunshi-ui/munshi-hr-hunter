from __future__ import annotations

import json

import pytest

from app.linkedin_employer_link_resolver_v1 import (
    ats_from_url,
    external_url,
    location_agrees,
    official_feed_jobs,
    resolve_stored,
)
from app.linkedin_public_ats_feed_v1 import (
    fetch_official_jobs,
    resolve_from_verified_board,
    verified_board_endpoint,
)


def job(**updates):
    row = {
        "source": "JobSpy/linkedin",
        "job_url": "https://www.linkedin.com/jobs/view/4455550001",
        "ats_job_id": "4455550001",
        "company_name": "Example Group",
        "title": "HR Analyst",
        "location_raw": "New York, NY",
        "description_raw": "Handle employee surveys, salary analysis and compensation trends using spreadsheets. "
        "Work with payroll partners to improve pay transparency. Review job architecture and benefits.",
        "apply_url": None,
    }
    return {**row, **updates}


def employer(**updates):
    row = {
        "source": "Greenhouse",
        "company_name": "Example Group",
        "title": "HR Analyst",
        "location_raw": "New York, NY",
        "job_url": "https://job-boards.greenhouse.io/example/jobs/11011",
        "apply_url": "https://job-boards.greenhouse.io/example/jobs/11011",
        "description_raw": job()["description_raw"],
    }
    return {**row, **updates}


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "javascript:alert(1)",
        "http://example.com/path",
        "https://linkedin.com/jobs/view/12345",
        "https://www.indeed.com/viewjob?jk=123",
        "https://glassdoor.com/job-listing/example",
        "https://example.com@127.0.0.1/private",
        "https://user:pass@example.com/path",
        "https://127.0.0.1/internal",
        "https://10.1.1.1/internal",
        "https://localhost.localdomain/p",
        "https://host.internal/private",
        "https://example.com:8080/api",
    ],
)
def test_rejects_unsafe_or_aggregator_destinations(bad):
    assert external_url(bad) is None


def test_valid_ats_url_preserves_id_and_tracking():
    url = "https://jobs.lever.co/example/abc-123/apply?source=LinkedIn"
    assert external_url(url) == url
    assert ats_from_url(url) == "Lever"


def test_unknown_when_no_other_job():
    result = resolve_stored(job(), [])
    assert result.resolution_status == "UNKNOWN"
    assert result.employer_apply_url is None
    assert result.linkedin_job_url.endswith("/4455550001")


def test_cross_source_is_candidate_not_auto_verified():
    result = resolve_stored(job(), [employer()])
    assert result.resolution_status == "CANDIDATE"
    assert result.employer_apply_url.endswith("/11011")
    assert result.ats_platform == "Greenhouse"
    assert result.evidence["needs_employer_confirmation"] is True


def test_company_title_mismatch_never_auto_matched():
    result = resolve_stored(job(), [employer(title="Compensation Manager")])
    assert result.resolution_status == "UNKNOWN"


def test_location_mismatch_never_auto_matched():
    result = resolve_stored(job(), [employer(location_raw="Chicago, IL")])
    assert result.resolution_status == "UNKNOWN"


def test_existing_direct_link_is_unverified_candidate():
    result = resolve_stored(job(apply_url="https://jobs.ashbyhq.com/example/abc"), [])
    assert result.resolution_status == "CANDIDATE"
    assert result.resolution_source == "stored_direct_url"


def test_legacy_linkedin_apply_url_not_used_as_external():
    result = resolve_stored(
        job(apply_url="https://www.linkedin.com/jobs/view/4455550001"), []
    )
    assert result.resolution_status == "UNKNOWN"


def test_ambiguous_conflicting_external_links():
    result = resolve_stored(
        job(),
        [
            employer(),
            employer(apply_url="https://jobs.ashbyhq.com/example/abcd"),
        ],
    )
    assert result.resolution_status == "AMBIGUOUS"
    assert result.employer_apply_url is None
    assert result.evidence["candidate_count"] == 2


def test_official_unique_matching_job_verified_with_corroborating_description():
    result = resolve_stored(
        job(), [employer()], official=True, official_provider="Greenhouse"
    )
    assert result.resolution_status == "VERIFIED"
    assert result.evidence["identity_evidence"] == "strong"


def test_official_same_title_but_insufficient_description_not_verified():
    result = resolve_stored(
        job(),
        [employer(description_raw="")],
        official=True,
        official_provider="Greenhouse",
    )
    assert result.resolution_status == "CANDIDATE"


def test_matching_requisition_overrides_missing_description():
    result = resolve_stored(
        job(requisition_id="REQ-451"),
        [employer(requisition_id="REQ-451", description_raw="")],
        official=True,
        official_provider="Greenhouse",
    )
    assert result.resolution_status == "VERIFIED"


def test_conflicting_requisition_rejected():
    result = resolve_stored(
        job(requisition_id="REQ-451"),
        [employer(requisition_id="REQ-459")],
        official=True,
        official_provider="Greenhouse",
    )
    assert result.resolution_status == "UNKNOWN"


@pytest.mark.parametrize(
    "provider,body,expected",
    [
        (
            "Greenhouse",
            {
                "jobs": [
                    {
                        "title": "HR Analyst",
                        "location": {"name": "New York, NY"},
                        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/11011",
                        "content": "<p>Compensation survey research.</p>",
                    }
                ]
            },
            "https://job-boards.greenhouse.io/example/jobs/11011",
        ),
        (
            "Lever",
            [
                {
                    "text": "HR Analyst",
                    "categories": {"location": "New York, NY"},
                    "applyUrl": "https://jobs.lever.co/example/abc/apply",
                    "hostedUrl": "https://jobs.lever.co/example/abc",
                    "descriptionPlain": "Compensation survey research.",
                }
            ],
            "https://jobs.lever.co/example/abc/apply",
        ),
        (
            "Ashby",
            {
                "jobs": [
                    {
                        "title": "HR Analyst",
                        "location": "New York, NY",
                        "isListed": True,
                        "applyUrl": "https://jobs.ashbyhq.com/example/abc/application",
                        "jobUrl": "https://jobs.ashbyhq.com/example/abc",
                    }
                ]
            },
            "https://jobs.ashbyhq.com/example/abc/application",
        ),
        (
            "SmartRecruiters",
            {
                "content": [
                    {
                        "name": "HR Analyst",
                        "location": {"city": "New York", "region": "NY"},
                        "applyUrl": "https://jobs.smartrecruiters.com/example/100",
                    }
                ]
            },
            "https://jobs.smartrecruiters.com/example/100",
        ),
    ],
)
def test_official_feed_normalizes_explicit_application_urls(provider, body, expected):
    rows = official_feed_jobs(provider, body, "Example Group")
    assert len(rows) == 1
    assert rows[0]["apply_url"] == expected


def test_ashby_unlisted_jobs_never_appear():
    assert (
        official_feed_jobs(
            "Ashby",
            {
                "jobs": [
                    {
                        "title": "Hidden",
                        "applyUrl": "https://jobs.ashbyhq.com/x/hidden",
                        "isListed": False,
                    }
                ]
            },
            "Example Group",
        )
        == []
    )


@pytest.mark.parametrize(
    "name", ["../internal", "thing/foo", "company?admin=1", "", "abc.example.com"]
)
def test_board_names_must_not_be_guessed_or_allow_arbitrary_urls(name):
    with pytest.raises(ValueError):
        verified_board_endpoint("Ashby", name)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()
        self.headers = {"Content-Length": str(len(self.payload))}

    def read(self, _limit):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def open(self, request, timeout):
        assert timeout == 7
        assert request.get_method() == "GET"
        self.urls.append(request.full_url)
        return FakeResponse(self.payload)


def test_official_api_fetch_is_get_only_to_fixed_provider_host():
    opener = FakeOpener(
        {
            "jobs": [
                {
                    "title": "HR Analyst",
                    "location": "New York, NY",
                    "isListed": True,
                    "applyUrl": "https://jobs.ashbyhq.com/example/abc",
                }
            ]
        }
    )
    rows = fetch_official_jobs("Ashby", "example", "Example Group", opener=opener)
    assert len(rows) == 1
    assert opener.urls == ["https://api.ashbyhq.com/posting-api/job-board/example"]


def test_official_board_employer_must_match():
    result = resolve_from_verified_board(
        job(),
        provider="Ashby",
        board_name="example",
        verified_employer_name="Different Co",
        opener=FakeOpener({}),
    )
    assert result.resolution_status == "UNAVAILABLE"
    assert result.evidence["reason"] == "employer_board_mismatch"


def test_linkedin_job_url_is_immutable_during_resolution():
    posting = job()["job_url"]
    row = job()
    _ = resolve_stored(row, [employer()])
    assert row["job_url"] == posting
    assert row["apply_url"] is None


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("Rockaway, NJ", "Rockaway, NJ, US", True),
        ("New York, NY", "New York, New York, USA", True),
        ("San Francisco, CA", "San Francisco, California", True),
        ("New York, NY", "New York City", True),
        ("San Francisco, CA", "San Francisco", True),
        ("Pittsburgh, PA", "Pittsburgh", True),
        ("San Francisco, CA", "New York", False),
        ("San Francisco, CA", "San Francisco, CA • New York, NY • United States", True),
        ("Chicago, IL", "San Francisco, CA • New York, NY • United States", False),
        ("New York, NY", "New York, NY & Pittsburgh, PA", True),
        ("Hackensack, NJ", "Perth Amboy, NJ", False),
        ("Remote", "New York, NY", False),
    ],
)
def test_conservative_location_normalization(a, b, expected):
    assert location_agrees(a, b) is expected


def test_verified_board_identity_requires_matching_employer_host_and_source():
    from app.linkedin_public_ats_feed_v1 import verified_board_from_existing_job

    row = {
        "company_name": "Braze",
        "source": "Greenhouse/braze",
        "job_url": "https://job-boards.greenhouse.io/braze/jobs/12345",
    }
    assert verified_board_from_existing_job("Braze", row) == ("Greenhouse", "braze")
    assert verified_board_from_existing_job("Other employer", row) is None
    assert (
        verified_board_from_existing_job(
            "Braze",
            {**row, "job_url": "https://malicious.example.com/braze/jobs/12345"},
        )
        is None
    )
    assert (
        verified_board_from_existing_job(
            "Braze", {**row, "source": "Greenhouse/another"}
        )
        is None
    )


def test_verified_board_rejects_untrusted_employer_source_pair():
    from app.linkedin_public_ats_feed_v1 import verified_board_from_existing_job

    assert (
        verified_board_from_existing_job(
            "Example Group",
            {
                "company_name": "Example Group",
                "source": "LinkedIn",
                "job_url": "https://jobs.ashbyhq.com/example/123",
            },
        )
        is None
    )


def test_multi_location_official_record_can_match_its_real_city():
    result = resolve_stored(
        job(location_raw="San Francisco, CA"),
        [
            employer(
                location_raw="San Francisco, CA • New York, NY • United States",
                description_raw=job()["description_raw"],
            )
        ],
        official=True,
        official_provider="Greenhouse",
    )
    assert result.resolution_status == "VERIFIED"
