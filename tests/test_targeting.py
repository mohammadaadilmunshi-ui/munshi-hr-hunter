from __future__ import annotations

import pytest

from app.targeting import PrimaryCategory, evaluate_job, filter_jobs, load_rules


@pytest.mark.parametrize(
    ("job", "expected"),
    [
        (
            {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Austin, TX", "country": "US"},
            PrimaryCategory.ELIGIBLE.value,
        ),
        (
            {"title": "Patient Recruitment Coordinator", "company_name": "Clinic", "location_raw": "Boston, MA", "country": "US"},
            PrimaryCategory.REJECT_ROLE.value,
        ),
        (
            {"title": "Strategic Sourcing Analyst", "company_name": "Acme", "location_raw": "Dallas, TX", "country": "US"},
            PrimaryCategory.REJECT_ROLE.value,
        ),
        (
            {"title": "Benefits Product Analyst", "company_name": "Acme", "location_raw": "Chicago, IL", "country": "US"},
            PrimaryCategory.REJECT_ROLE.value,
        ),
        (
            {"title": "Carwash/Driver $17.50 hr SBA", "company_name": "Acme", "location_raw": "Santa Barbara, CA", "country": "US"},
            PrimaryCategory.REJECT_ROLE.value,
        ),
        (
            {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Remote - United States", "country": "US", "qualifications": "Three years preferred, but not required."},
            PrimaryCategory.ELIGIBLE.value,
        ),
        (
            {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Remote - United States", "country": "US", "qualifications": "Requires a minimum of three years of HR experience."},
            PrimaryCategory.REJECT_HARD_REQUIREMENT.value,
        ),
        (
            {"title": "HR Analyst", "company_name": "Acme", "location_raw": "San Diego, CA", "country": "US", "description_raw": "RESPONSIBILITIES: Analyze data. REQUIREMENTS: * 5+ years of HR generalist experience * Strong analytics skills."},
            PrimaryCategory.REJECT_HARD_REQUIREMENT.value,
        ),
        (
            {"title": "Senior HR Operations Analyst", "company_name": "Acme", "location_raw": "Remote - United States", "country": "US"},
            PrimaryCategory.REJECT_HARD_REQUIREMENT.value,
        ),
        (
            {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Berlin, Germany", "country": "DE"},
            PrimaryCategory.REJECT_LOCATION.value,
        ),
        (
            {"title": "Recruiting Coordinator", "company_name": "Acme", "location_raw": "Worldwide", "remote_type": "Remote"},
            PrimaryCategory.REJECT_LOCATION.value,
        ),
        (
            {"title": "Recruiting Coordinator", "company_name": "Blocked Example", "location_raw": "Miami, FL", "country": "US"},
            PrimaryCategory.REJECT_COMPANY.value,
        ),
        (
            {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Columbus, OH", "country": "US", "qualifications": "Benefits vest after 3 years of service."},
            PrimaryCategory.ELIGIBLE.value,
        ),
        (
            {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Seattle, WA", "country": "US", "description_raw": "Navigation: Software Engineer role requires 5 years of experience. This job analyzes workforce metrics."},
            PrimaryCategory.ELIGIBLE.value,
        ),
        (
            {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Remote - United States", "country": "US", "qualifications": "Requires at least 3 years in human resources."},
            PrimaryCategory.REJECT_HARD_REQUIREMENT.value,
        ),
        (
            {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Austin, TX", "country": "US", "provider_country": "DE"},
            PrimaryCategory.REJECT_LOCATION.value,
        ),
        (
            {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Remote", "remote_type": "Remote"},
            PrimaryCategory.REJECT_LOCATION.value,
        ),
    ],
)
def test_canonical_targeting(job: dict[str, object], expected: str, hunter_db) -> None:
    decision = evaluate_job(job, load_rules())
    assert decision["primary_category"] == expected


def test_preferences_never_limit_us_eligibility(hunter_db) -> None:
    texas = evaluate_job(
        {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Austin, TX", "country": "US"}
    )
    new_jersey = evaluate_job(
        {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Newark, NJ", "state": "NJ", "country": "US"}
    )
    assert texas["accepted"] is True
    assert texas["preference"]["score"] == 0
    assert new_jersey["accepted"] is True
    assert new_jersey["preference"]["score"] == 20


def test_hourly_pay_token_does_not_hide_a_genuine_hr_role(hunter_db) -> None:
    decision = evaluate_job(
        {
            "title": "HR Coordinator - $25/hr",
            "company_name": "Acme",
            "location_raw": "Newark, NJ",
            "country": "US",
        }
    )
    assert decision["primary_category"] == PrimaryCategory.ELIGIBLE.value


def test_role_reject_skips_expensive_description_requirement_scan(
    hunter_db, monkeypatch
) -> None:
    from app import targeting

    def must_not_scan(*_args, **_kwargs):
        raise AssertionError("requirement scan ran after conclusive role rejection")

    monkeypatch.setattr(targeting, "hard_requirement_evidence", must_not_scan)
    decision = targeting.evaluate_job(
        {
            "title": "Backend Software Engineer",
            "company_name": "Acme",
            "location_raw": "Austin, TX",
            "country": "US",
            "description_raw": "A very large non-HR job description.",
        }
    )
    assert decision["primary_category"] == PrimaryCategory.REJECT_ROLE.value
    assert decision["hard_requirement_evidence"]["evaluation_skipped"] == (
        "primary_role_rejected"
    )


def test_exclusive_funnel_has_zero_delta(hunter_db) -> None:
    eligible = {"title": "People Analytics Analyst", "company_name": "Acme", "location_raw": "Austin, TX", "country": "US", "apply_url": "https://example.test/jobs/1"}
    jobs = [
        eligible,
        dict(eligible),
        {"title": "Patient Recruitment Coordinator", "company_name": "Clinic", "location_raw": "Boston, MA", "country": "US"},
        {"title": "HR Operations Analyst", "company_name": "Acme", "location_raw": "Remote - United States", "country": "US", "qualifications": "At least 3 years required."},
        {"title": "Recruiting Coordinator", "company_name": "Acme", "location_raw": "Toronto, Canada", "country": "CA"},
        {"title": "Recruiting Coordinator", "company_name": "Blocked Example", "location_raw": "Miami, FL", "country": "US"},
    ]
    funnel = filter_jobs(jobs)
    assert funnel["accounting_delta"] == 0
    assert funnel["raw_normalized"] == sum(funnel["primary_counts"].values())
    assert funnel["primary_counts"][PrimaryCategory.DUPLICATE.value] == 1
    assert funnel["primary_counts"][PrimaryCategory.ELIGIBLE.value] == 1
