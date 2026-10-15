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
