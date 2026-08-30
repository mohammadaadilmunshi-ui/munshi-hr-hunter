from __future__ import annotations

import json
from typing import Any

from app.discovery_config import (
    build_location_search_plan,
    load_target_roles,
)
from app.hunter_worker import (
    matches_location_rule,
)
from app.relevance import (
    match_target_role,
)
from app.sources.smartrecruiters import (
    fetch_all_postings,
    fetch_posting_detail,
    normalize_posting,
)


COMPANY_IDENTIFIER = (
    "smartrecruiters"
)


def main() -> None:
    roles = load_target_roles()
    location_plan = (
        build_location_search_plan()
    )

    fetched = fetch_all_postings(
        COMPANY_IDENTIFIER,
        max_pages=10,
    )

    raw_postings = fetched[
        "postings"
    ]

    excluded_by_role = 0
    excluded_by_location = 0
    role_candidates: list[
        dict[str, Any]
    ] = []

    for summary in raw_postings:
        title = str(
            summary.get("name")
            or ""
        )

        matched, target_role, reason = (
            match_target_role(
                title,
                roles,
            )
        )

        if not matched:
            excluded_by_role += 1
            continue

        normalized_stub = (
            normalize_posting(
                summary,
                None,
                registry_company_name=(
                    "SmartRecruiters"
                ),
                company_identifier=(
                    COMPANY_IDENTIFIER
                ),
            )
        )

        location_matches = []

        for rule in location_plan:
            location_matched, (
                location_reason
            ) = matches_location_rule(
                normalized_stub,
                rule,
            )

            if location_matched:
                location_matches.append(
                    {
                        "rule": rule.get(
                            "rule_name"
                        ),
                        "reason": (
                            location_reason
                        ),
                    }
                )

        if not location_matches:
            excluded_by_location += 1
            continue

        role_candidates.append(
            {
                "summary": summary,
                "target_role": (
                    target_role
                ),
                "role_reason": reason,
                "location_matches": (
                    location_matches
                ),
            }
        )

    eligible_jobs = []
    detail_requests = 0
    detail_errors = []

    for candidate in role_candidates:
        summary = candidate[
            "summary"
        ]

        posting_id = str(
            summary.get("id")
            or summary.get("uuid")
            or ""
        ).strip()

        if not posting_id:
            detail_errors.append(
                {
                    "title": (
                        summary.get("name")
                    ),
                    "error": (
                        "missing_posting_id"
                    ),
                }
            )
            continue

        try:
            detail_result = (
                fetch_posting_detail(
                    COMPANY_IDENTIFIER,
                    posting_id,
                )
            )

            detail_requests += 1

            normalized = (
                normalize_posting(
                    summary,
                    detail_result[
                        "posting"
                    ],
                    registry_company_name=(
                        "SmartRecruiters"
                    ),
                    company_identifier=(
                        COMPANY_IDENTIFIER
                    ),
                )
            )

            eligible_jobs.append(
                {
                    "title": normalized[
                        "title"
                    ],
                    "company": normalized[
                        "company_name"
                    ],
                    "location": normalized[
                        "location_raw"
                    ],
                    "remote_type": (
                        normalized[
                            "remote_type"
                        ]
                    ),
                    "employment_type": (
                        normalized[
                            "employment_type"
                        ]
                    ),
                    "target_role": (
                        candidate[
                            "target_role"
                        ]
                    ),
                    "location_matches": (
                        candidate[
                            "location_matches"
                        ]
                    ),
                    "apply_url": (
                        normalized[
                            "apply_url"
                        ]
                    ),
                }
            )

        except Exception as error:
            detail_errors.append(
                {
                    "posting_id": (
                        posting_id
                    ),
                    "title": (
                        summary.get("name")
                    ),
                    "error": str(error),
                }
            )

    result = {
        "success": not bool(
            detail_errors
            and not eligible_jobs
            and role_candidates
        ),
        "mode": (
            "smartrecruiters-"
            "live-dry-run"
        ),
        "source": (
            "SmartRecruiters"
        ),
        "company_identifier": (
            COMPANY_IDENTIFIER
        ),
        "target_role_count": len(
            roles
        ),
        "location_rule_count": len(
            location_plan
        ),
        "list_network_requests": (
            fetched[
                "network_requests"
            ]
        ),
        "detail_network_requests": (
            detail_requests
        ),
        "network_request_made": (
            fetched[
                "network_requests"
            ]
            + detail_requests
            > 0
        ),
        "raw_jobs_found": len(
            raw_postings
        ),
        "excluded_by_role": (
            excluded_by_role
        ),
        "excluded_by_location": (
            excluded_by_location
        ),
        "eligible_jobs": len(
            eligible_jobs
        ),
        "eligible_sample": (
            eligible_jobs[:10]
        ),
        "pages": fetched["pages"],
        "detail_errors": (
            detail_errors
        ),
        "database_writes": 0,
        "telegram_messages": 0,
        "source_notifications": 0,
        "n8n_calls": 0,
    }

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
