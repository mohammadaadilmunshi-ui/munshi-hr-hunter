from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from typing import Any
from urllib.parse import (
    parse_qsl,
    urlencode,
    urlparse,
    urlunparse,
)

from app.dedupe_policy import dedupe_keeper_allowed



DEDUPE_VERSION = "universal_dedupe_v2"

COMPANY_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "limited",
    "plc",
    "holdings",
}

AGGREGATOR_HOSTS = {
    "indeed.com",
    "www.indeed.com",
    "linkedin.com",
    "www.linkedin.com",
    "glassdoor.com",
    "www.glassdoor.com",
    "ziprecruiter.com",
    "www.ziprecruiter.com",
}

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "source",
    "src",
    "ref",
    "referrer",
    "iis",
    "iisn",
    "gh_src",
}

DESCRIPTION_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "this",
    "to",
    "we",
    "will",
    "with",
    "you",
    "your",
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize(
        "NFKD",
        str(value or ""),
    )

    text = "".join(
        character
        for character in text
        if not unicodedata.combining(
            character
        )
    )

    text = (
        text.replace("\\-", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("−", "-")
        .casefold()
    )

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def text_tokens(value: Any) -> list[str]:
    return re.findall(
        r"[a-z0-9]+",
        normalize_text(value),
    )


def canonical_company(value: Any) -> str:
    tokens = text_tokens(value)

    while (
        tokens
        and tokens[-1] in COMPANY_SUFFIXES
    ):
        tokens.pop()

    return "".join(tokens)


def canonical_title(value: Any) -> str:
    text = normalize_text(value)

    text = re.sub(
        r"\bhr\b",
        "human resources",
        text,
    )

    text = re.sub(
        r"\bta\b",
        "talent acquisition",
        text,
    )

    return "".join(
        text_tokens(text)
    )


def canonical_location(
    job: dict[str, Any],
) -> str:
    city = "".join(
        text_tokens(
            job.get("city")
        )
    )

    state = "".join(
        text_tokens(
            job.get("state")
        )
    )

    country = "".join(
        text_tokens(
            job.get("country")
        )
    )

    structured = "".join(
        value
        for value in (
            city,
            state,
            country,
        )
        if value
    )

    if structured:
        return structured

    return "".join(
        text_tokens(
            job.get("location_raw")
        )
    )


def salary_signature(
    job: dict[str, Any],
) -> str:
    hourly_min = job.get(
        "normalized_hourly_min"
    )

    hourly_max = job.get(
        "normalized_hourly_max"
    )

    if (
        hourly_min is not None
        or hourly_max is not None
    ):
        return (
            f"hourly:"
            f"{hourly_min or ''}:"
            f"{hourly_max or ''}"
        )

    salary_text = normalize_text(
        job.get("salary_raw")
    )

    if not salary_text:
        return ""

    numbers = re.findall(
        r"\d+(?:\.\d+)?",
        salary_text,
    )

    period = ""

    for candidate in (
        "hour",
        "year",
        "month",
        "week",
        "day",
    ):
        if candidate in salary_text:
            period = candidate
            break

    currency = ""

    for candidate in (
        "usd",
        "eur",
        "gbp",
        "cad",
    ):
        if candidate in salary_text:
            currency = candidate
            break

    return "|".join(
        [
            currency,
            period,
            *numbers[:4],
        ]
    )


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()

    if not text:
        return None

    text = text[:10]

    try:
        return datetime.strptime(
            text,
            "%Y-%m-%d",
        ).date()
    except ValueError:
        return None


def posting_week(value: Any) -> str:
    parsed = parse_date(value)

    if parsed is None:
        return ""

    iso = parsed.isocalendar()

    return (
        f"{iso.year}-W"
        f"{iso.week:02d}"
    )


def date_gap_days(
    first: Any,
    second: Any,
) -> int | None:
    first_date = parse_date(first)
    second_date = parse_date(second)

    if (
        first_date is None
        or second_date is None
    ):
        return None

    return abs(
        (first_date - second_date).days
    )


def canonical_url(value: Any) -> str:
    text = str(value or "").strip()

    if not text:
        return ""

    try:
        parsed = urlparse(text)
    except ValueError:
        return ""

    host = parsed.netloc.casefold()

    if host.startswith("www."):
        host = host[4:]

    query = [
        (key, value)
        for key, value in parse_qsl(
            parsed.query,
            keep_blank_values=False,
        )
        if key.casefold()
        not in TRACKING_PARAMETERS
    ]

    return urlunparse(
        (
            parsed.scheme.casefold(),
            host,
            parsed.path.rstrip("/"),
            "",
            urlencode(sorted(query)),
            "",
        )
    )


def is_aggregator_url(value: Any) -> bool:
    text = str(value or "").strip()

    if not text:
        return False

    try:
        host = urlparse(
            text
        ).netloc.casefold()
    except ValueError:
        return False

    if host.startswith("www."):
        host = host[4:]

    return host in {
        host_value.removeprefix("www.")
        for host_value
        in AGGREGATOR_HOSTS
    }


def is_direct_employer_url(value: Any) -> bool:
    text = str(value or "").strip()

    if not text:
        return False

    try:
        host = urlparse(
            text
        ).netloc.casefold()
    except ValueError:
        return False

    return bool(
        host
        and not is_aggregator_url(text)
    )


def choose_preferred_url(
    existing_value: Any,
    incoming_value: Any,
) -> str | None:
    existing = str(
        existing_value or ""
    ).strip()

    incoming = str(
        incoming_value or ""
    ).strip()

    if not existing:
        return incoming or None

    if not incoming:
        return existing

    existing_direct = (
        is_direct_employer_url(existing)
    )

    incoming_direct = (
        is_direct_employer_url(incoming)
    )

    if incoming_direct and not existing_direct:
        return incoming

    if existing_direct and not incoming_direct:
        return existing

    return (
        incoming
        if len(incoming) > len(existing)
        else existing
    )


def description_tokens(
    job: dict[str, Any],
) -> set[str]:
    combined = " ".join(
        [
            str(
                job.get(
                    "description_raw"
                )
                or ""
            ),
            str(
                job.get(
                    "qualifications"
                )
                or ""
            ),
            str(
                job.get(
                    "responsibilities"
                )
                or ""
            ),
        ]
    )

    return {
        token
        for token in text_tokens(combined)
        if (
            len(token) >= 3
            and token
            not in DESCRIPTION_STOPWORDS
        )
    }


def description_similarity(
    first: dict[str, Any],
    second: dict[str, Any],
) -> float:
    first_tokens = description_tokens(
        first
    )

    second_tokens = description_tokens(
        second
    )

    if (
        not first_tokens
        or not second_tokens
    ):
        return 0.0

    intersection = len(
        first_tokens & second_tokens
    )

    union = len(
        first_tokens | second_tokens
    )

    if union == 0:
        return 0.0

    return intersection / union


def semantic_key(
    job: dict[str, Any],
) -> tuple[str, str, str]:
    return (
        canonical_company(
            job.get("company_name")
        ),
        canonical_title(
            job.get("title")
        ),
        canonical_location(job),
    )


def create_universal_job_fingerprint(
    job: dict[str, Any],
) -> str:
    payload = {
        "company": canonical_company(
            job.get("company_name")
        ),
        "title": canonical_title(
            job.get("title")
        ),
        "location": (
            canonical_location(job)
        ),
        "salary": salary_signature(job),
        "posting_week": posting_week(
            job.get("date_posted")
        ),
    }

    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    return hashlib.sha256(
        encoded.encode("utf-8")
    ).hexdigest()


def disambiguated_fingerprint(
    job: dict[str, Any],
) -> str:
    base = (
        create_universal_job_fingerprint(
            job
        )
    )

    extra = "|".join(
        [
            str(
                job.get("ats_job_id")
                or ""
            ),
            canonical_url(
                job.get("apply_url")
                or job.get("job_url")
            ),
            normalize_text(
                job.get(
                    "description_raw"
                )
            )[:1200],
        ]
    )

    suffix = hashlib.sha256(
        extra.encode("utf-8")
    ).hexdigest()[:20]

    return f"{base}:distinct:{suffix}"


def duplicate_evidence(
    first: dict[str, Any],
    second: dict[str, Any],
) -> dict[str, Any] | None:
    if semantic_key(first) != semantic_key(
        second
    ):
        return None

    first_salary = salary_signature(
        first
    )

    second_salary = salary_signature(
        second
    )

    salary_match = bool(
        first_salary
        and second_salary
        and first_salary == second_salary
    )

    salary_conflict = bool(
        first_salary
        and second_salary
        and first_salary != second_salary
    )

    similarity = description_similarity(
        first,
        second,
    )

    date_gap = date_gap_days(
        first.get("date_posted"),
        second.get("date_posted"),
    )

    first_url = (
        first.get("apply_url")
        or first.get("job_url")
    )

    second_url = (
        second.get("apply_url")
        or second.get("job_url")
    )

    same_url = bool(
        canonical_url(first_url)
        and canonical_url(first_url)
        == canonical_url(second_url)
    )

    aggregator_direct_pair = (
        is_aggregator_url(first_url)
        != is_aggregator_url(second_url)
    )

    reasons: list[str] = []

    if same_url:
        reasons.append(
            "same_canonical_url"
        )

    if similarity >= 0.72:
        reasons.append(
            "high_description_similarity"
        )

    if (
        salary_match
        and similarity >= 0.35
    ):
        reasons.append(
            "same_salary_and_description"
        )

    if (
        salary_match
        and date_gap is not None
        and date_gap <= 14
    ):
        reasons.append(
            "same_salary_and_posting_window"
        )

    if (
        aggregator_direct_pair
        and similarity >= 0.45
    ):
        reasons.append(
            "aggregator_direct_copy"
        )

    if (
        not first_salary
        and not second_salary
        and date_gap is not None
        and date_gap <= 7
        and similarity >= 0.55
    ):
        reasons.append(
            "same_week_and_description"
        )

    if salary_conflict and similarity < 0.90:
        return None

    if not reasons:
        return None

    return {
        "reasons": reasons,
        "description_similarity": round(
            similarity,
            4,
        ),
        "salary_match": salary_match,
        "date_gap_days": date_gap,
        "aggregator_direct_pair": (
            aggregator_direct_pair
        ),
    }


def find_semantic_duplicate(
    connection,
    job: dict[str, Any],
):
    city = str(
        job.get("city") or ""
    ).strip()

    state = str(
        job.get("state") or ""
    ).strip()

    location_raw = str(
        job.get("location_raw") or ""
    ).strip()

    if city and state:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE
                lower(
                    COALESCE(city, '')
                ) = lower(?)
                AND lower(
                    COALESCE(state, '')
                ) = lower(?)
                AND lower(
                    COALESCE(status, 'found')
                ) != 'duplicate'
            ORDER BY id DESC
            LIMIT 500
            """,
            (
                city,
                state,
            ),
        ).fetchall()

    elif location_raw:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE
                lower(
                    COALESCE(
                        location_raw,
                        ''
                    )
                ) = lower(?)
                AND lower(
                    COALESCE(status, 'found')
                ) != 'duplicate'
            ORDER BY id DESC
            LIMIT 500
            """,
            (location_raw,),
        ).fetchall()

    else:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE lower(
                COALESCE(status, 'found')
            ) != 'duplicate'
            ORDER BY id DESC
            LIMIT 500
            """
        ).fetchall()

    best = None

    # Load the current dashboard rules once for this candidate set. A historical
    # row that is no longer targeted must not suppress a corrected rediscovery.
    try:
        from app.dashboard_targeting_gate import load_dashboard_targeting_rules

        dedupe_rules = load_dashboard_targeting_rules()
    except Exception:
        dedupe_rules = None

    for row in rows:
        candidate = dict(row)

        if not dedupe_keeper_allowed(candidate, rules=dedupe_rules):
            continue

        evidence = duplicate_evidence(
            candidate,
            job,
        )

        if evidence is None:
            continue

        score = (
            len(evidence["reasons"]),
            evidence[
                "description_similarity"
            ],
        )

        if (
            best is None
            or score > best[0]
        ):
            best = (
                score,
                row,
                evidence,
            )

    if best is None:
        return None

    return best[1], best[2]


def keeper_rank(
    job: dict[str, Any],
) -> tuple[int, int, int, int, int]:
    direct_apply = int(
        is_direct_employer_url(
            job.get("apply_url")
        )
    )

    already_applied = int(
        job.get("already_applied")
        or 0
    )

    source_quality = -int(
        job.get("source_tier")
        or 99
    )

    description_length = len(
        str(
            job.get(
                "description_raw"
            )
            or ""
        )
    )

    return (
        already_applied,
        direct_apply,
        source_quality,
        description_length,
        int(job["id"]),
    )


def migrate_existing_jobs(
    connection,
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY id
            """
        ).fetchall()
    ]

    groups: dict[
        tuple[str, str, str],
        list[dict[str, Any]],
    ] = defaultdict(list)

    for job in rows:
        groups[
            semantic_key(job)
        ].append(job)

    duplicate_groups = []
    duplicate_count = 0
    used_fingerprints: set[str] = set()

    connection.execute(
        "BEGIN IMMEDIATE"
    )

    for job in rows:
        temporary = (
            f"universal-migration:"
            f"{job['id']}:"
            f"{hashlib.sha256(str(job['id']).encode()).hexdigest()}"
        )

        connection.execute(
            """
            UPDATE jobs
            SET job_fingerprint = ?
            WHERE id = ?
            """,
            (
                temporary,
                int(job["id"]),
            ),
        )

    for group_jobs in groups.values():
        parent = {
            int(job["id"]): int(job["id"])
            for job in group_jobs
        }

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[
                    parent[value]
                ]
                value = parent[value]
            return value

        def union(
            first: int,
            second: int,
        ) -> None:
            first_root = find(first)
            second_root = find(second)

            if first_root != second_root:
                parent[second_root] = (
                    first_root
                )

        for first_index in range(
            len(group_jobs)
        ):
            for second_index in range(
                first_index + 1,
                len(group_jobs),
            ):
                first = group_jobs[
                    first_index
                ]

                second = group_jobs[
                    second_index
                ]

                if duplicate_evidence(
                    first,
                    second,
                ):
                    union(
                        int(first["id"]),
                        int(second["id"]),
                    )

        components: dict[
            int,
            list[dict[str, Any]],
        ] = defaultdict(list)

        for job in group_jobs:
            components[
                find(int(job["id"]))
            ].append(job)

        for members in components.values():
            keeper = max(
                members,
                key=keeper_rank,
            )

            keeper_id = int(
                keeper["id"]
            )

            keeper_fingerprint = (
                create_universal_job_fingerprint(
                    keeper
                )
            )

            if (
                keeper_fingerprint
                in used_fingerprints
            ):
                keeper_fingerprint = (
                    disambiguated_fingerprint(
                        keeper
                    )
                )

            used_fingerprints.add(
                keeper_fingerprint
            )

            connection.execute(
                """
                UPDATE jobs
                SET
                    job_fingerprint = ?,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    keeper_fingerprint,
                    keeper_id,
                ),
            )

            if len(members) == 1:
                continue

            member_ids = sorted(
                int(member["id"])
                for member in members
            )

            duplicate_groups.append(
                {
                    "keeper_job_id": (
                        keeper_id
                    ),
                    "member_job_ids": (
                        member_ids
                    ),
                }
            )

            for member in members:
                member_id = int(
                    member["id"]
                )

                if member_id == keeper_id:
                    continue

                duplicate_count += 1

                prior_reason = str(
                    member.get(
                        "hard_rejection_reason"
                    )
                    or ""
                ).strip()

                duplicate_reason = (
                    f"duplicate_of_job_"
                    f"{keeper_id}"
                )

                final_reason = (
                    duplicate_reason
                    if not prior_reason
                    else (
                        prior_reason
                        + " | "
                        + duplicate_reason
                    )
                )

                try:
                    breakdown = json.loads(
                        member.get(
                            "score_breakdown_json"
                        )
                        or "{}"
                    )
                except (
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    breakdown = {}

                breakdown.update(
                    {
                        "dedupe_version": (
                            DEDUPE_VERSION
                        ),
                        "canonical_duplicate": (
                            True
                        ),
                        "duplicate_of_job_id": (
                            keeper_id
                        ),
                        "final_score": 0.0,
                    }
                )

                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        job_fingerprint = ?,
                        hunter_score = 0,
                        match_label =
                            'DUPLICATE',
                        status = 'duplicate',
                        hard_rejection_reason =
                            ?,
                        score_breakdown_json =
                            ?,
                        scoring_version = ?,
                        last_scored_at =
                            CURRENT_TIMESTAMP,
                        updated_at =
                            CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        (
                            keeper_fingerprint
                            + ":duplicate:"
                            + str(member_id)
                        ),
                        final_reason,
                        json.dumps(
                            breakdown,
                            ensure_ascii=False,
                        ),
                        DEDUPE_VERSION,
                        member_id,
                    ),
                )

    payload = {
        "success": True,
        "jobs_examined": len(rows),
        "duplicate_groups": (
            duplicate_groups
        ),
        "duplicate_count": (
            duplicate_count
        ),
        "telegram_messages": 0,
        "n8n_calls": 0,
        "dedupe_version": DEDUPE_VERSION,
    }

    connection.execute(
        """
        INSERT INTO events (
            job_id,
            event_type,
            actor,
            event_status,
            payload_json
        )
        VALUES (
            NULL,
            'universal_duplicate_migration',
            'terminal_patch',
            'completed',
            ?
        )
        """,
        (
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
        ),
    )

    connection.commit()

    return payload


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--migrate",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.migrate:
        raise SystemExit(
            "Use --migrate to process "
            "existing jobs."
        )

    connection = sqlite3.connect(
        "data/hunter.db"
    )

    connection.row_factory = sqlite3.Row

    try:
        result = migrate_existing_jobs(
            connection
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
