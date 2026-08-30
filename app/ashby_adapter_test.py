from __future__ import annotations

import json

from app.sources.ashby import (
    parse_ashby_payload,
)


FIXTURE = {
    "apiVersion": "1",
    "jobs": [
        {
            "title": (
                "People Analytics Intern"
            ),
            "location": (
                "New York, NY"
            ),
            "secondaryLocations": [
                {
                    "location": (
                        "Philadelphia, PA"
                    ),
                    "address": {
                        "addressLocality": (
                            "Philadelphia"
                        ),
                        "addressRegion": (
                            "Pennsylvania"
                        ),
                        "addressCountry": (
                            "USA"
                        ),
                    },
                }
            ],
            "department": (
                "People Operations"
            ),
            "team": (
                "People Analytics"
            ),
            "isListed": True,
            "isRemote": False,
            "workplaceType": (
                "Hybrid"
            ),
            "descriptionHtml": (
                "<p>Support workforce reporting, "
                "HR dashboards, recruiting metrics, "
                "and people analytics projects.</p>"
            ),
            "descriptionPlain": (
                "Support workforce reporting, "
                "HR dashboards, recruiting metrics, "
                "and people analytics projects."
            ),
            "publishedAt": (
                "2026-07-02T16:00:00+00:00"
            ),
            "employmentType": "Intern",
            "address": {
                "postalAddress": {
                    "addressLocality": (
                        "New York"
                    ),
                    "addressRegion": (
                        "New York"
                    ),
                    "addressCountry": (
                        "USA"
                    ),
                }
            },
            "jobUrl": (
                "https://jobs.ashbyhq.com/"
                "fixture-company/"
                "ashby-test-123"
            ),
            "applyUrl": (
                "https://jobs.ashbyhq.com/"
                "fixture-company/"
                "ashby-test-123/apply"
            ),
            "compensation": {
                "compensationTierSummary": (
                    "$22 - $28 per hour"
                ),
                "scrapeableCompensationSalarySummary": (
                    "$22 - $28 per hour"
                ),
                "summaryComponents": [
                    {
                        "compensationType": (
                            "Salary"
                        ),
                        "interval": (
                            "1 HOUR"
                        ),
                        "currencyCode": (
                            "USD"
                        ),
                        "minValue": 22,
                        "maxValue": 28,
                    }
                ],
            },
        },
        {
            "title": (
                "Unlisted Internal Position"
            ),
            "location": (
                "New York, NY"
            ),
            "isListed": False,
            "employmentType": (
                "FullTime"
            ),
        },
    ],
}


def main() -> None:
    jobs = parse_ashby_payload(
        company_name=(
            "Ashby Fixture Company"
        ),
        board_name=(
            "fixture-company"
        ),
        payload=FIXTURE,
    )

    assert len(jobs) == 1

    job = jobs[0]

    assert (
        job["source"]
        == "Ashby/fixture-company"
    )
    assert (
        job["company_name"]
        == "Ashby Fixture Company"
    )
    assert (
        job["title"]
        == "People Analytics Intern"
    )
    assert job["city"] == "New York"
    assert job["state"] == "NY"
    assert job["country"] == "US"

    assert (
        job["remote_type"]
        == "Hybrid"
    )

    assert (
        job["employment_type"]
        == "Internship"
    )

    assert (
        job[
            "normalized_hourly_min"
        ]
        == 22.0
    )

    assert (
        job[
            "normalized_hourly_max"
        ]
        == 28.0
    )

    assert (
        job["salary_raw"]
        == "$22 - $28 per hour"
    )

    assert (
        job["ats_job_id"]
        == "ashby-test-123"
    )

    assert job["apply_url"]

    assert (
        len(
            job[
                "ashby_secondary_locations"
            ]
        )
        == 1
    )

    assert (
        job[
            "ashby_secondary_locations"
        ][0]["state"]
        == "PA"
    )

    output = {
        "success": True,
        "mode": (
            "ashby-zero-network-test"
        ),
        "network_request_made": False,
        "database_writes": 0,
        "telegram_messages": 0,
        "source_run_notifications": 0,
        "n8n_calls": 0,
        "jobs_in_fixture": 2,
        "listed_jobs_parsed": len(
            jobs
        ),
        "unlisted_jobs_excluded": 1,
        "sample": {
            "source": job["source"],
            "company": (
                job["company_name"]
            ),
            "title": job["title"],
            "location": (
                job["location_raw"]
            ),
            "city": job["city"],
            "state": job["state"],
            "country": job["country"],
            "workplace_type": (
                job["remote_type"]
            ),
            "employment_type": (
                job["employment_type"]
            ),
            "salary_raw": (
                job["salary_raw"]
            ),
            "hourly_min": (
                job[
                    "normalized_hourly_min"
                ]
            ),
            "hourly_max": (
                job[
                    "normalized_hourly_max"
                ]
            ),
            "secondary_locations": (
                job[
                    "ashby_secondary_locations"
                ]
            ),
            "apply_url_present": bool(
                job["apply_url"]
            ),
            "description_present": bool(
                job["description_raw"]
            ),
        },
    }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
