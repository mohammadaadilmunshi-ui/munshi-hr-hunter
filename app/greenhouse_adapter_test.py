from __future__ import annotations

import json

from app.sources.greenhouse import (
    parse_greenhouse_payload,
)


FIXTURE = {
    "jobs": [
        {
            "id": 123456,
            "title": (
                "Human Resources Intern"
            ),
            "updated_at": (
                "2026-07-02T10:30:00-04:00"
            ),
            "location": {
                "name": (
                    "New York, NY, "
                    "United States"
                )
            },
            "absolute_url": (
                "https://boards.greenhouse.io/"
                "example/jobs/123456"
            ),
            "content": (
                "<p>Support recruiting, "
                "candidate scheduling, ATS records, "
                "and onboarding.</p>"
            ),
            "departments": [
                {
                    "id": 10,
                    "name": (
                        "Human Resources"
                    ),
                }
            ],
            "offices": [
                {
                    "id": 20,
                    "name": "New York",
                    "location": (
                        "New York, NY, "
                        "United States"
                    ),
                }
            ],
        }
    ],
    "meta": {
        "total": 1,
    },
}


def main() -> None:
    jobs = parse_greenhouse_payload(
        company_name=(
            "Greenhouse Fixture Company"
        ),
        board_token="fixture-company",
        payload=FIXTURE,
    )

    assert len(jobs) == 1

    job = jobs[0]

    assert (
        job["source"]
        == "Greenhouse/fixture-company"
    )
    assert (
        job["company_name"]
        == "Greenhouse Fixture Company"
    )
    assert (
        job["title"]
        == "Human Resources Intern"
    )
    assert job["city"] == "New York"
    assert job["state"] == "NY"
    assert job["country"] == "US"
    assert (
        "<p>"
        not in job["description_raw"]
    )
    assert job["apply_url"]
    assert (
        job["source_tier"]
        == 1
    )

    output = {
        "success": True,
        "mode": (
            "greenhouse-zero-network-test"
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
            "apply_url_present": bool(
                job["apply_url"]
            ),
            "description_html_removed": (
                "<" not in
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
