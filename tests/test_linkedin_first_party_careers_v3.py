from __future__ import annotations

import pytest

from app.linkedin_first_party_careers_v3 import (
    ApprovedEmployerSite,
    candidate_job_urls_from_official_html,
    description_url_hints,
    discover_from_approved_html,
    employer_search_queries,
    strict_public_hostname,
    validate_approved_site,
    validated_first_party_url,
)


def approved(**changes):
    return ApprovedEmployerSite(
        company_name=changes.get("company_name", "Example Inc"),
        official_domain=changes.get("official_domain", "example.com"),
        careers_url=changes.get("careers_url", "https://careers.example.com/jobs/"),
        approval_source=changes.get("approval_source", "human_verified"),
        approval_reference=changes.get("approval_reference", "operator-review-123"),
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "127.0.0.1",
        "10.0.0.7",
        "localhost",
        "api.internal",
        "example.com.evil.host@127.0.0.1",
        "example..com",
        "192.0.2.1",
        "example.com:80",
        "metadata.google.internal",
        "-corp.com",
    ],
)
def test_untrusted_hosts_rejected(value):
    assert strict_public_hostname(value) is None


def test_first_party_must_match_company_root():
    assert (
        validated_first_party_url("https://careers.example.com/jobs/1", "example.com")
        == "https://careers.example.com/jobs/1"
    )
    assert (
        validated_first_party_url(
            "https://careers.example.com.evil.com/jobs/1", "example.com"
        )
        is None
    )
    assert validated_first_party_url("http://example.com/jobs/1", "example.com") is None
    assert (
        validated_first_party_url("https://example.com:8443/jobs", "example.com")
        is None
    )
    assert (
        validated_first_party_url("https://user:pass@example.com/jobs", "example.com")
        is None
    )


def test_domain_approval_is_mandatory():
    with pytest.raises(ValueError):
        validate_approved_site(approved(approval_source="web_search_result"))
    with pytest.raises(ValueError):
        validate_approved_site(approved(approval_reference=""))
    with pytest.raises(ValueError):
        validate_approved_site(approved(careers_url="https://example.evil.com/jobs"))


def test_approved_official_html_produces_only_board_hints():
    html = """<a href="https://jobs.ashbyhq.com/example/123">Apply</a>
    <iframe src="https://jobs.lever.co/example"></iframe>
    <a href="https://jobs.ashbyhq.com.evil.com/example/abc">Malicious</a>"""
    result = discover_from_approved_html(approved(), html)
    assert result.evidence_status == "HINTS_ONLY"
    assert result.evidence["application_url_verified"] is False
    assert {(x.provider, x.board_name) for x in result.board_hints} == {
        ("Ashby", "example"),
        ("Lever", "example"),
    }


def test_official_careers_anchor_is_job_page_hint_not_apply_authority():
    html = """<a href="/jobs/req-123">HR Analyst</a>
    <a href="https://careers.example.com/jobs/req-456">Another job</a>
    <a href="https://attacker.example.org/jobs/secret">Fake</a>"""
    rows = candidate_job_urls_from_official_html(approved(), html)
    assert len(rows) == 2
    assert all(x["employer_apply_url"] == "" for x in rows)
    assert all(x["status"] == "CANDIDATE" for x in rows)


def test_description_link_even_ats_is_unverified():
    desc = """
    Apply: https://jobs.ashbyhq.com/example/job-123
    Docs: https://example.com/privacy.
    False: https://localhost/internal
    Tracking: https://www.linkedin.com/jobs/view/123
    """
    links = description_url_hints(desc)
    assert any(x["ats_provider_hint"] == "Ashby" for x in links)
    assert all(x["confidence"] == "UNVERIFIED_DESCRIPTION_HINT" for x in links)
    assert not any(
        "localhost" in x["host"] or "linkedin.com" in x["host"] for x in links
    )


def test_search_queries_are_nonexecuting_plain_strings():
    terms = employer_search_queries("Example Inc", "HR Analyst")
    assert len(terms) == 2
    assert terms[0].startswith("Example Inc official careers")
    assert not employer_search_queries("", "HR Analyst")


def test_large_snapshot_is_rejected():
    with pytest.raises(ValueError, match="too large"):
        discover_from_approved_html(approved(), "x" * 3_000_001)


def linkedin_job(**changes):
    return {
        "company_name": "Example Inc",
        "title": "HR Analyst",
        "location_raw": "New York, NY",
        "job_url": "https://www.linkedin.com/jobs/view/4455550011",
        "ats_job_id": "4455550011",
        "description_raw": (
            "Build people dashboards and track employee compensation trends. "
            "Partner with payroll and recruiting to improve HR analytics reporting. "
            "Review benefits strategy, headcount forecasting and performance data."
        ),
        **changes,
    }


def test_official_site_resolution_defaults_to_no_network():
    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job(), approved(), allow_network=False
    )
    assert result.resolution_status == "UNKNOWN"
    assert result.evidence["reason"] == "network_opt_in_required"


def test_wrong_company_rejected_before_network_request():
    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job(company_name="Other Company"), approved(), allow_network=True
    )
    assert result.resolution_status == "UNAVAILABLE"


def test_static_js_shell_is_not_assumed_easy_apply():
    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job(),
        approved(),
        allow_network=True,
        approved_site_fetcher=lambda _site: (
            "<html><script src='/app.js'></script></html>"
        ),
    )
    assert result.resolution_status == "UNKNOWN"
    assert result.evidence["needs_controlled_renderer"] is True


def test_multiple_boards_are_ambiguous_and_never_submitted():
    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    html = (
        "<a href='https://job-boards.greenhouse.io/example/jobs/123'></a>"
        "<a href='https://jobs.ashbyhq.com/example/abc'></a>"
    )
    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job(),
        approved(),
        allow_network=True,
        approved_site_fetcher=lambda _site: html,
    )
    assert result.resolution_status == "AMBIGUOUS"
    assert result.employer_apply_url is None


def test_first_party_site_to_exact_official_ats_job_e2e_mock():
    import json

    from app.linkedin_first_party_careers_v3 import (
        resolve_linkedin_job_from_approved_careers,
    )

    description = linkedin_job()["description_raw"]

    class FakeResponse:
        status = 200

        def __init__(self, payload):
            self.raw = json.dumps(payload).encode()
            self.headers = {"Content-Length": str(len(self.raw))}

        def read(self, limit):
            return self.raw[:limit]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    class FakeATS:
        def __init__(self):
            self.called = []

        def open(self, request, timeout):
            assert request.get_method() == "GET"
            assert timeout == 7
            url = request.full_url
            self.called.append(url)
            if url.endswith("/jobs"):
                return FakeResponse(
                    {
                        "jobs": [
                            {
                                "id": 123,
                                "title": "HR Analyst",
                                "location": {"name": "New York, NY • Pittsburgh, PA"},
                                "absolute_url": "https://job-boards.greenhouse.io/example/jobs/123",
                            }
                        ]
                    }
                )
            if url.endswith("/jobs/123?content=true"):
                return FakeResponse({"id": 123, "content": f"<p>{description}</p>"})
            raise AssertionError("Unexpected network operation")

    ats = FakeATS()
    result = resolve_linkedin_job_from_approved_careers(
        linkedin_job(),
        approved(),
        allow_network=True,
        approved_site_fetcher=lambda _site: (
            "<a href='https://job-boards.greenhouse.io/example/jobs/123'>Role</a>"
        ),
        ats_opener=ats,
    )
    assert result.resolution_status == "VERIFIED"
    assert result.ats_platform == "Greenhouse"
    assert (
        result.employer_apply_url == "https://job-boards.greenhouse.io/example/jobs/123"
    )
    assert result.evidence["ats_board_from_official_website"] is True
    assert result.evidence["submit_authorized"] is False
    assert len(ats.called) == 2
