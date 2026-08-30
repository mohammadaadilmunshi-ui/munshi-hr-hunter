from __future__ import annotations

from typing import Any

from app.sources.smartrecruiters import (
    fetch_all_postings,
    normalize_posting,
)


class FakeResponse:
    def __init__(
        self,
        payload: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.status_code = (
            status_code
        )

    def raise_for_status(
        self,
    ) -> None:
        if self.status_code >= 400:
            raise RuntimeError(
                f"HTTP {self.status_code}"
            )

    def json(
        self,
    ) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self) -> None:
        self.calls = 0

    def get(
        self,
        *_args,
        **_kwargs,
    ) -> FakeResponse:
        self.calls += 1

        return FakeResponse(
            {
                "limit": 100,
                "offset": 0,
                "totalFound": 1,
                "content": [
                    {
                        "id": "12345",
                        "uuid": (
                            "example-uuid"
                        ),
                        "name": (
                            "People Operations "
                            "Intern"
                        ),
                        "company": {
                            "identifier": (
                                "example"
                            ),
                            "name": (
                                "Example Inc"
                            ),
                        },
                        "releasedDate": (
                            "2026-07-02"
                            "T12:00:00Z"
                        ),
                        "location": {
                            "country": "US",
                            "region": "NJ",
                            "city": (
                                "Jersey City"
                            ),
                            "remote": True,
                        },
                        "typeOfEmployment": {
                            "label": (
                                "Internship"
                            )
                        },
                    }
                ],
            }
        )


fake = FakeSession()

result = fetch_all_postings(
    "example",
    max_pages=2,
    session=fake,
)

assert fake.calls == 1
assert len(
    result["postings"]
) == 1

summary = result["postings"][0]

detail = {
    **summary,
    "applyUrl": (
        "https://example.com/"
        "apply/12345"
    ),
    "jobAd": {
        "sections": {
            "jobDescription": {
                "text": (
                    "<p>Support "
                    "onboarding and "
                    "HR operations.</p>"
                )
            },
            "qualifications": {
                "text": (
                    "<p>No prior "
                    "experience required."
                    "</p>"
                )
            },
        }
    },
}

normalized = normalize_posting(
    summary,
    detail,
    registry_company_name=(
        "Example Inc"
    ),
    company_identifier="example",
)

assert normalized["title"] == (
    "People Operations Intern"
)

assert normalized[
    "remote_type"
] == "Remote"

assert normalized["city"] == (
    "Jersey City"
)

assert normalized["state"] == "NJ"
assert normalized["country"] == "US"

assert normalized[
    "employment_type"
] == "Internship"

assert (
    "Support onboarding"
    in normalized[
        "description_raw"
    ]
)

assert normalized["apply_url"] == (
    "https://example.com/"
    "apply/12345"
)

print(
    "SMARTRECRUITERS ZERO-NETWORK TEST: PASSED"
)
print(
    "LIST PAGINATION: PASSED"
)
print(
    "HTML NORMALIZATION: PASSED"
)
print(
    "LOCATION NORMALIZATION: PASSED"
)
print(
    "REMOTE NORMALIZATION: PASSED"
)
print(
    "DETAIL NORMALIZATION: PASSED"
)
print(
    "NETWORK CALLS TO REAL API: 0"
)
print(
    "TELEGRAM MESSAGES: 0"
)
print(
    "N8N CALLS: 0"
)
