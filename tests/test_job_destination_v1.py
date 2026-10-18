from __future__ import annotations

import pytest

from app.job_destination_v1 import describe_job_destination


@pytest.mark.parametrize(
    ("apply_url", "provider"),
    [
        ("https://example.wd5.myworkdayjobs.com/en-US/Careers/job/R-100", "Workday"),
        ("https://boards.greenhouse.io/example/jobs/100", "Greenhouse"),
        ("https://jobs.lever.co/example/100", "Lever"),
        ("https://jobs.ashbyhq.com/example/100", "Ashby"),
        ("https://jobs.smartrecruiters.com/Example/100-role", "SmartRecruiters"),
    ],
)
def test_known_provider_is_detected_from_generic_url_patterns(apply_url: str, provider: str) -> None:
    result = describe_job_destination(
        {"apply_url": apply_url, "job_url": apply_url, "source": "Manual"}
    )

    assert result.provider == provider
    assert result.apply_via == provider
    assert result.original_url == apply_url
    assert result.confidence == "high"


def test_indeed_discovery_is_separate_from_direct_ats_destination() -> None:
    result = describe_job_destination(
        {
            "source": "Indeed via JobSpy",
            "job_url": "https://www.indeed.com/viewjob?jk=abc",
            "apply_url": "https://acme.wd1.myworkdayjobs.com/Careers/job/R-200",
        }
    )

    assert result.source == "Indeed"
    assert result.apply_via == "Workday"
    assert result.provider == "Workday"
    assert result.original_url.endswith("/R-200")
    assert "JobSpy" not in result.apply_via


def test_unknown_employer_site_uses_domain_without_fabricating_ats() -> None:
    result = describe_job_destination(
        {
            "source": "Company feed",
            "apply_url": "https://careers.example.org/openings/people-analyst",
        }
    )

    assert result.provider is None
    assert result.apply_via == "Employer’s website · careers.example.org"
    assert result.confidence == "domain_only"
    assert result.original_url == "https://careers.example.org/openings/people-analyst"


def test_employer_job_url_outranks_aggregator_apply_url() -> None:
    result = describe_job_destination(
        {
            "source": "Indeed via JobSpy",
            "apply_url": "https://www.indeed.com/viewjob?jk=source-copy",
            "job_url": "https://careers.example.org/openings/people-analyst",
        }
    )

    assert result.original_url == "https://careers.example.org/openings/people-analyst"
    assert result.source == "Indeed"
    assert result.provider is None


def test_jobspy_is_never_fabricated_as_an_ats_provider() -> None:
    result = describe_job_destination(
        {
            "source_provenance_json": '[{"source":"Indeed via JobSpy"}]',
            "ats_provider": "JobSpy",
            "apply_url": "https://careers.example.org/openings/people-analyst",
        }
    )

    assert result.provider is None
    assert result.apply_via == "Employer’s website · careers.example.org"
    assert result.source == "Indeed"


def test_invalid_or_missing_urls_are_never_invented() -> None:
    result = describe_job_destination(
        {"source": "JobSpy", "apply_url": "javascript:alert(1)", "job_url": ""}
    )

    assert result.original_url is None
    assert result.provider is None
    assert result.apply_via == "Destination not recorded"


def test_linkedin_without_employer_destination_does_not_claim_linkedin_is_ats():
    result = describe_job_destination({'source': 'JobSpy/linkedin',
        'job_url': 'https://www.linkedin.com/jobs/view/4470721097', 'apply_url': None})
    assert result.source == 'LinkedIn'
    assert result.provider is None
    assert result.apply_via == 'Employer link pending'


@pytest.mark.parametrize(('url', 'name'), [
    ('https://acme.bamboohr.com/careers/12', 'BambooHR'),
    ('https://recruiting.paylocity.com/Recruiting/Jobs/Details/12', 'Paylocity'),
    ('https://jobs.lever.co/acme/12?source=linkedin', 'Lever'),
])
def test_linkedin_ats_is_identified_from_external_destination(url, name):
    result = describe_job_destination({'source': 'JobSpy/linkedin',
        'job_url': 'https://www.linkedin.com/jobs/view/4470721097', 'apply_url': url})
    assert result.source == 'LinkedIn'
    assert result.apply_via == name
    assert result.original_url == url
