from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from typing import Any

from app.database import get_connection, get_setting


SCORING_VERSION = "rules_v1"


TRACK_KEYWORDS = {
    "Talent Acquisition": [
        "talent acquisition",
        "recruiting",
        "recruiter",
        "candidate sourcing",
        "interview scheduling",
    ],
    "HRIS": [
        "hris",
        "workday",
        "successfactors",
        "bamboohr",
        "human resources information system",
    ],
    "People Analytics": [
        "people analytics",
        "hr analytics",
        "workforce analytics",
        "employee analytics",
        "people data",
    ],
    "HR Operations": [
        "hr operations",
        "human resources operations",
        "onboarding",
        "employee records",
        "hr administration",
    ],
    "People Operations": [
        "people operations",
        "people ops",
        "employee experience",
    ],
    "Global Mobility / Immigration": [
        "global mobility",
        "immigration",
        "visa",
        "relocation",
    ],
    "General HR": [
        "human resources",
        "hr intern",
        "hr assistant",
        "hr coordinator",
    ],
}


CPT_TRAPDOOR_TERMS = [
    "immediate start",
    "start immediately",
    "available immediately",
    "must start immediately",
    "start asap",
    "asap start",
    "urgently hiring",
    "summer internship",
    "start in june",
    "start in july",
]


CITIZEN_ONLY_TERMS = [
    "u.s. citizens only",
    "us citizens only",
    "must be a u.s. citizen",
    "must be a us citizen",
    "u.s. citizenship required",
    "us citizenship required",
]


GREEN_CARD_ONLY_TERMS = [
    "green card holders only",
    "green card holder only",
    "permanent residents only",
    "permanent resident only",
]


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def contains_any(text: str, terms: list[str]) -> list[str]:
    return [
        term
        for term in terms
        if normalize_text(term) in text
    ]


def parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()

    if not text or text.lower() in {
        "not specified",
        "not provided",
        "immediate",
    }:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def classify_track(
    title_text: str,
    job_text: str,
) -> tuple[str, dict[str, int]]:
    match_counts: dict[str, int] = {}

    for track, keywords in TRACK_KEYWORDS.items():
        match_counts[track] = sum(
            1
            for keyword in keywords
            if keyword in job_text
        )

    title_priorities = [
        (
            "People Analytics",
            [
                "people analytics",
                "hr analytics",
                "workforce analytics",
                "talent analytics",
            ],
        ),
        (
            "HRIS",
            [
                "hris",
                "human resources information system",
            ],
        ),
        (
            "Talent Acquisition",
            [
                "talent acquisition",
                "recruiting coordinator",
                "recruitment coordinator",
                "recruiter",
                "recruiting intern",
            ],
        ),
        (
            "HR Operations",
            [
                "hr operations",
                "human resources operations",
            ],
        ),
        (
            "People Operations",
            [
                "people operations",
                "people ops",
            ],
        ),
        (
            "Global Mobility / Immigration",
            [
                "global mobility",
                "immigration",
            ],
        ),
        (
            "General HR",
            [
                "human resources intern",
                "hr intern",
                "hr assistant",
                "hr coordinator",
            ],
        ),
    ]

    # Job-title matches take priority over generic words
    # appearing elsewhere in the posting.
    for track, title_patterns in title_priorities:
        if any(
            pattern in title_text
            for pattern in title_patterns
        ):
            return track, match_counts

    best_track = max(
        match_counts,
        key=match_counts.get,
    )

    if match_counts[best_track] == 0:
        best_track = "General HR"

    return best_track, match_counts


def calculate_location_score(
    job: dict[str, Any],
) -> tuple[int, str | None]:
    location_text = normalize_text(
        job.get("location_raw")
    )

    remote_type = normalize_text(
        job.get("remote_type")
    )

    connection = get_connection()

    try:
        rules = connection.execute(
            """
            SELECT *
            FROM location_rules
            WHERE is_active = 1
            ORDER BY priority_weight DESC
            """
        ).fetchall()
    finally:
        connection.close()

    best_score = -5
    best_match: str | None = None

    for rule in rules:
        arrangement_allowed = True

        if "remote" in remote_type:
            arrangement_allowed = bool(
                rule["remote_allowed"]
            )
        elif "hybrid" in remote_type:
            arrangement_allowed = bool(
                rule["hybrid_allowed"]
            )
        elif "onsite" in remote_type:
            arrangement_allowed = bool(
                rule["onsite_allowed"]
            )

        if not arrangement_allowed:
            continue

        location_match = False

        rule_name = normalize_text(
            rule["location_name"]
        )

        rule_city = normalize_text(
            rule["city"]
        )

        rule_state = normalize_text(
            rule["state"]
        )

        if rule_name and rule_name in location_text:
            location_match = True

        if rule_city and rule_city in location_text:
            location_match = True

        if rule_state:
            state_pattern = rf"\b{re.escape(rule_state)}\b"

            if re.search(state_pattern, location_text):
                location_match = True

        if (
            "remote" in remote_type
            and bool(rule["remote_allowed"])
            and (
                "remote" in rule_name
                or rule["location_type"] == "Region"
            )
        ):
            location_match = True

        if location_match:
            rule_score = max(
                0,
                min(
                    int(rule["priority_weight"] or 0),
                    20,
                ),
            )

            if rule_score > best_score:
                best_score = rule_score
                best_match = rule["location_name"]

    return best_score, best_match


def calculate_salary_score(
    job: dict[str, Any],
    scoring: dict[str, Any],
) -> tuple[int, str]:
    minimum_required = float(
        scoring.get("min_hourly_rate", 18.0)
    )

    minimum_salary = job.get(
        "normalized_hourly_min"
    )

    maximum_salary = job.get(
        "normalized_hourly_max"
    )

    salary_raw = normalize_text(
        job.get("salary_raw")
    )

    if "unpaid" in salary_raw:
        return -40, "unpaid"

    if minimum_salary is None and maximum_salary is None:
        if scoring.get("allow_unknown_salary", True):
            return 5, "unknown_allowed"

        return -5, "unknown_penalty"

    if (
        minimum_salary is not None
        and float(minimum_salary) >= minimum_required
    ):
        return 8, "meets_minimum"

    if (
        maximum_salary is not None
        and float(maximum_salary) >= minimum_required
    ):
        return 4, "partially_meets_minimum"

    return -8, "below_minimum"


def score_job(job: dict[str, Any]) -> dict[str, Any]:
    scoring = get_setting("scoring", {})
    authorization = get_setting(
        "authorization",
        {},
    )
    targeting = get_setting("targeting", {})
    canonical_gate = job.get("_targeting_decision")
    canonical_accepted = bool(
        isinstance(canonical_gate, dict)
        and canonical_gate.get("canonical_targeting_gate")
        and canonical_gate.get("accepted")
    )

    combined_text = normalize_text(
        " ".join(
            [
                str(job.get("title") or ""),
                str(job.get("description_raw") or ""),
                str(job.get("responsibilities") or ""),
                str(job.get("qualifications") or ""),
                str(job.get("preferred_skills") or ""),
                str(job.get("work_authorization") or ""),
                str(job.get("start_date") or ""),
            ]
        )
    )

    title_text = normalize_text(
        job.get("title")
    )

    company_text = normalize_text(
        job.get("company_name")
    )

    authorization_text = normalize_text(
        " ".join(
            [
                str(job.get("work_authorization") or ""),
                str(job.get("description_raw") or ""),
            ]
        )
    )

    hard_rejection_reason: str | None = None

    salary_raw = normalize_text(
        job.get("salary_raw")
    )

    if (
        scoring.get("reject_unpaid", True)
        and (
            "unpaid" in salary_raw
            or "unpaid internship" in combined_text
        )
    ):
        hard_rejection_reason = "Unpaid role"

    citizen_matches = contains_any(
        authorization_text,
        CITIZEN_ONLY_TERMS,
    )

    if (
        not hard_rejection_reason
        and authorization.get(
            "reject_citizen_only",
            True,
        )
        and citizen_matches
    ):
        hard_rejection_reason = (
            "U.S. citizenship required"
        )

    green_card_matches = contains_any(
        authorization_text,
        GREEN_CARD_ONLY_TERMS,
    )

    if (
        not hard_rejection_reason
        and authorization.get(
            "reject_green_card_only",
            True,
        )
        and green_card_matches
    ):
        hard_rejection_reason = (
            "Permanent-resident restriction"
        )

    blacklist = {
        normalize_text(company)
        for company in targeting.get(
            "company_blacklist",
            [],
        )
    }

    if (
        not hard_rejection_reason
        and company_text in blacklist
    ):
        hard_rejection_reason = (
            "Company is blacklisted"
        )

    if canonical_accepted:
        canonical_role = canonical_gate.get("role_evidence") or {}
        canonical_family = str(canonical_role.get("target_family") or "").strip()
        target_track = canonical_family or "Configured target role"
        track_matches = {target_track: 1}
    else:
        target_track, track_matches = classify_track(title_text, combined_text)

    target_roles = [
        normalize_text(role)
        for role in targeting.get(
            "target_roles",
            [],
        )
    ]

    if title_text in target_roles:
        role_score = 18
        role_reason = "exact_target_role"
    elif any(
        role and (
            role in title_text
            or title_text in role
        )
        for role in target_roles
    ):
        role_score = 15
        role_reason = "partial_target_role"
    elif track_matches.get(target_track, 0) > 0:
        role_score = 12
        role_reason = "target_track_match"
    else:
        role_score = 0
        role_reason = "no_target_role_match"

    location_score, location_match = (
        calculate_location_score(job)
    )

    boosted_keywords = [
        normalize_text(keyword)
        for keyword in targeting.get(
            "boosted_keywords",
            [],
        )
    ]

    matched_boosted_keywords = [
        keyword
        for keyword in boosted_keywords
        if keyword and keyword in combined_text
    ]

    skills_score = min(
        len(matched_boosted_keywords) * 2,
        12,
    )

    salary_score, salary_reason = (
        calculate_salary_score(
            job,
            scoring,
        )
    )

    if re.search(r"\b(?:cpt|opt)\b", authorization_text):
        authorization_score = 8
        authorization_reason = (
            "explicit_cpt_or_opt"
        )
    elif (
        "work authorization" in authorization_text
        or "authorized to work" in authorization_text
    ):
        authorization_score = 2
        authorization_reason = (
            "generic_work_authorization"
        )
    else:
        authorization_score = 5
        authorization_reason = (
            "authorization_not_specified"
        )

    posted_date = parse_iso_date(
        job.get("date_posted")
    )

    freshness_score = 0
    age_days: int | None = None
    ghost_risk_score = 0

    if posted_date is not None:
        age_days = max(
            (date.today() - posted_date).days,
            0,
        )

        if age_days <= 2:
            freshness_score = 4
        elif age_days <= 7:
            freshness_score = 2
        elif age_days <= 21:
            freshness_score = -5
            ghost_risk_score = 25
        elif age_days <= 45:
            freshness_score = -15
            ghost_risk_score = 60
        else:
            freshness_score = -25
            ghost_risk_score = 90

    watchlist = {
        normalize_text(company)
        for company in targeting.get(
            "company_watchlist",
            [],
        )
    }

    company_score = (
        10 if company_text in watchlist else 0
    )

    rejected_keywords = [
        normalize_text(keyword)
        for keyword in targeting.get(
            "rejected_keywords",
            [],
        )
    ]

    matched_rejected_keywords: list[str] = []

    for keyword in rejected_keywords:
        if not keyword:
            continue

        if keyword in {
            "director",
            "vice president",
        }:
            matched = keyword in title_text
        else:
            matched = keyword in combined_text

        if matched:
            matched_rejected_keywords.append(
                keyword
            )

    penalty_score = min(
        len(matched_rejected_keywords) * 10,
        30,
    )

    base_score = 30

    raw_score = (
        base_score
        + role_score
        + location_score
        + skills_score
        + salary_score
        + authorization_score
        + freshness_score
        + company_score
        - penalty_score
    )

    trapdoor_matches = contains_any(
        combined_text,
        CPT_TRAPDOOR_TERMS,
    )
    if str(targeting.get("mode") or "").strip().upper() == "OPT":
        trapdoor_matches = []

    cpt_trapdoor = int(
        bool(trapdoor_matches)
    )

    # Eligibility has already been decided by the canonical targeting gate.
    # Legacy scoring remains a ranker and may not overturn that decision.
    if canonical_accepted:
        hard_rejection_reason = None

    final_score = max(
        0,
        min(int(round(raw_score)), 100),
    )

    if cpt_trapdoor:
        final_score = min(
            final_score,
            int(
                authorization.get(
                    "immediate_start_score_cap",
                    84,
                )
            ),
        )

    if age_days is not None and age_days > 45:
        final_score = min(
            final_score,
            int(
                scoring.get(
                    "ghost_job_score_cap",
                    92,
                )
            ),
        )

    status = "found"

    if hard_rejection_reason:
        final_score = 0
        status = "rejected"
        match_label = "REJECTED"
    elif final_score >= int(
        scoring.get("auto_n8n_threshold", 93)
    ):
        match_label = "URGENT MATCH"
    elif final_score >= int(
        scoring.get(
            "telegram_high_alert_threshold",
            85,
        )
    ):
        match_label = "HIGH MATCH"
    elif final_score >= 75:
        match_label = "GOOD MATCH"
    else:
        match_label = "LOW PRIORITY"

    if cpt_trapdoor and not hard_rejection_reason:
        match_label = (
            f"{match_label} - CPT REVIEW"
        )

    breakdown = {
        "scoring_version": SCORING_VERSION,
        "base_score": base_score,
        "role_score": role_score,
        "role_reason": role_reason,
        "location_score": location_score,
        "location_match": location_match,
        "skills_score": skills_score,
        "matched_boosted_keywords": (
            matched_boosted_keywords
        ),
        "salary_score": salary_score,
        "salary_reason": salary_reason,
        "authorization_score": authorization_score,
        "authorization_reason": authorization_reason,
        "freshness_score": freshness_score,
        "age_days": age_days,
        "company_score": company_score,
        "penalty_score": penalty_score,
        "matched_rejected_keywords": (
            matched_rejected_keywords
        ),
        "track_matches": track_matches,
        "cpt_trapdoor_terms": trapdoor_matches,
        "ghost_risk_score": ghost_risk_score,
        "raw_score": raw_score,
        "final_score": final_score,
        "hard_rejection_reason": (
            hard_rejection_reason
        ),
    }

    return {
        "target_track": target_track,
        "hunter_score": final_score,
        "match_label": match_label,
        "status": status,
        "cpt_trapdoor": cpt_trapdoor,
        "ghost_risk_score": ghost_risk_score,
        "hard_rejection_reason": (
            hard_rejection_reason
        ),
        "score_breakdown_json": json.dumps(
            breakdown,
            ensure_ascii=False,
        ),
        "scoring_version": SCORING_VERSION,
        "last_scored_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }
