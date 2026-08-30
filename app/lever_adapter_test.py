from __future__ import annotations

import json

from app.sources.lever import (
    parse_lever_payload,
    resolve_lever_api_root,
)


FIXTURE = [
    {
        "id": "lever-test-123",
        "text": (
            "Talent Acquisition Intern"
        ),
        "categories": {
            "location": (
                "New York, NY"
            ),
            "allLocations": [
                "New York, NY",
            ],
            "commitment": "Intern",
            "team": "People",
            "department": (
                "Human Resources"
            ),
            "level": "Internship",
        },
        "country": "US",
        "openingPlain": (
            "Support recruiting operations."
        ),
        "descriptionPlain": (
            "Support candidate sourcing, "
            "interview scheduling, ATS records, "
            "candidate communication, and onboarding."
        ),
        "lists": [
            {
                "text": (
                    "Responsibilities"
                ),
                "content": (
                    "<li>Coordinate interviews</li>"
                    "<li>Maintain ATS records</li>"
                ),
            },
            {
                "text": (
                    "Qualifications"
                ),
                "content": (
                    "<li>Strong communication</li>"
                    "<li>Excel skills</li>"
                ),
            },
            {
                "text": "Benefits",
                "content": (
                    "<li>Professional development</li>"
                ),
            },
        ],
        "additionalPlain": (
            "Equal opportunity employer."
        ),
        "hostedUrl": (
            "https://jobs.lever.co/"
            "fixture-company/"
            "lever-test-123"
        ),
        "applyUrl": (
            "https://jobs.lever.co/"
            "fixture-company/"
            "lever-test-123/apply"
        ),
        "workplaceType": "hybrid",
        "salaryRange": {
            "currency": "USD",
            "interval": (
                "per-hour-salary"
            ),
            "min": 22,
            "max": 28,
        },
        "salaryDescriptionPlain": (
            "$22 to $28 per hour"
        ),
        "createdAt": (
            "2026-07-02T12:00:00Z"
        ),
    }
]


def main() -> None:
    jobs = parse_lever_payload(
        company_name=(
            "Lever Fixture Company"
        ),
        site_name="fixture-company",
        payload=FIXTURE,
    )

    assert len(jobs) == 1

    job = jobs[0]

    assert (
        job["source"]
        == "Lever/fixture-company"
    )
    assert (
        job["company_name"]
        == "Lever Fixture Company"
    )
    assert (
        job["title"]
        == "Talent Acquisition Intern"
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
        == "Intern"
    )
    assert (
        job["normalized_hourly_min"]
        == 22.0
    )
    assert (
        job["normalized_hourly_max"]
        == 28.0
    )
    assert (
        job["responsibilities"]
    )
    assert job["qualifications"]
    assert job["benefits"]
    assert job["apply_url"]

    assert (
        resolve_lever_api_root(
            "https://jobs.lever.co/example"
        )
        == "https://api.lever.co/v0/postings"
    )

    assert (
        resolve_lever_api_root(
            "https://jobs.eu.lever.co/example"
        )
        == "https://api.eu.lever.co/v0/postings"
    )

    output = {
        "success": True,
        "mode": (
            "lever-zero-network-test"
        ),
        "network_request_made": False,
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "jobs_parsed": len(jobs),
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
            "apply_url_present": bool(
                job["apply_url"]
            ),
            "responsibilities_parsed": (
                bool(
                    job[
                        "responsibilities"
                    ]
                )
            ),
            "qualifications_parsed": (
                bool(
                    job[
                        "qualifications"
                    ]
                )
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
