from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.database import get_connection, initialize_database
from app.scoring import score_job


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def create_job_fingerprint(job: dict[str, Any]) -> str:
    fingerprint_input = "|".join(
        [
            normalize_text(job.get("company_name")),
            normalize_text(job.get("title")),
            normalize_text(job.get("location_raw")),
            normalize_text(job.get("description_raw"))[:800],
        ]
    )

    return hashlib.sha256(
        fingerprint_input.encode("utf-8")
    ).hexdigest()


def build_manual_job_text(job: dict[str, Any]) -> str:
    fields = [
        ("Job Title", job.get("title")),
        ("Company Name", job.get("company_name")),
        ("Location", job.get("location_raw")),
        (
            "Remote / Hybrid / Onsite",
            job.get("remote_type"),
        ),
        (
            "Internship / Part-Time / Full-Time",
            job.get("employment_type"),
        ),
        ("Pay / Wage / Salary", job.get("salary_raw")),
        ("Posted Date", job.get("date_posted")),
        ("Apply Deadline", job.get("apply_deadline")),
        ("Start Date", job.get("start_date")),
        ("End Date", job.get("end_date")),
        ("Hours per Week", job.get("hours_per_week")),
        ("Job Description", job.get("description_raw")),
        ("Responsibilities", job.get("responsibilities")),
        ("Qualifications", job.get("qualifications")),
        ("Preferred Skills", job.get("preferred_skills")),
        (
            "Work Authorization / Sponsorship",
            job.get("work_authorization"),
        ),
        ("Benefits", job.get("benefits")),
        ("Application Link", job.get("apply_url")),
        ("Recruiter / Contact", job.get("recruiter")),
        ("Recruiter Email", job.get("recruiter_email")),
        ("Company Size", job.get("company_size")),
        ("Industry", job.get("industry")),
        (
            "Employer Description",
            job.get("employer_description"),
        ),
    ]

    sections: list[str] = []

    for label, value in fields:
        cleaned_value = str(
            value
            if value not in (None, "")
            else "Not specified"
        ).strip()

        sections.append(f"{label}:\n{cleaned_value}")

    return "\n\n".join(sections)


FAKE_JOBS: list[dict[str, Any]] = [
    {
        "source": "Fake Worker",
        "source_tier": 0,
        "company_name": "Northstar Recruiting Labs",
        "title": "Talent Acquisition Intern",
        "location_raw": "New York, NY",
        "city": "New York",
        "state": "NY",
        "country": "US",
        "remote_type": "Hybrid",
        "employment_type": "Internship",
        "job_url": "https://example.com/jobs/northstar-ta-intern",
        "apply_url": "https://example.com/apply/northstar-ta-intern",
        "description_raw": (
            "Support candidate sourcing, interview scheduling, "
            "candidate communication, onboarding coordination, "
            "ATS record maintenance, and recruiting reports."
        ),
        "responsibilities": (
            "Coordinate interviews, maintain candidate records, "
            "support onboarding, and prepare recruiting reports."
        ),
        "qualifications": (
            "Current graduate student with strong communication, "
            "organization, and Excel skills."
        ),
        "preferred_skills": (
            "Excel, ATS, recruiting coordination, Power BI"
        ),
        "work_authorization": (
            "CPT eligible candidates may apply. "
            "No immediate sponsorship required."
        ),
        "benefits": "Not specified",
        "salary_raw": "$22 per hour",
        "normalized_hourly_min": 22.0,
        "normalized_hourly_max": 22.0,
        "salary_confidence": "high",
        "target_track": "Talent Acquisition",
        "hunter_score": 96,
        "match_label": "URGENT MATCH",
        "status": "found",
        "cpt_trapdoor": 0,
        "ghost_risk_score": 0,
        "date_posted": "2026-07-02",
        "apply_deadline": "Not specified",
        "start_date": "2026-08-28",
        "end_date": "2026-12-16",
        "hours_per_week": "20 hours per week",
        "recruiter": "Not provided",
        "recruiter_email": "Not provided",
        "company_size": "Not specified",
        "industry": "Technology",
        "employer_description": (
            "A fictional employer used only for system testing."
        ),
    },
    {
        "source": "Fake Worker",
        "source_tier": 0,
        "company_name": "People Metrics Studio",
        "title": "People Analytics Intern",
        "location_raw": "Remote - United States",
        "city": None,
        "state": None,
        "country": "US",
        "remote_type": "Remote",
        "employment_type": "Internship",
        "job_url": "https://example.com/jobs/people-analytics",
        "apply_url": "https://example.com/apply/people-analytics",
        "description_raw": (
            "Build workforce dashboards, clean HR datasets, "
            "analyze recruiting metrics, and support employee "
            "turnover reporting."
        ),
        "responsibilities": (
            "Prepare dashboards, analyze HR data, validate reports, "
            "and communicate workforce insights."
        ),
        "qualifications": (
            "Graduate student with Excel, data analysis, "
            "and visualization experience."
        ),
        "preferred_skills": (
            "Power BI, Tableau, Python, Excel, HR analytics"
        ),
        "work_authorization": (
            "Candidates must have current United States "
            "work authorization."
        ),
        "benefits": "Not specified",
        "salary_raw": "$20-$24 per hour",
        "normalized_hourly_min": 20.0,
        "normalized_hourly_max": 24.0,
        "salary_confidence": "high",
        "target_track": "People Analytics",
        "hunter_score": 89,
        "match_label": "HIGH MATCH",
        "status": "found",
        "cpt_trapdoor": 0,
        "ghost_risk_score": 0,
        "date_posted": "2026-07-01",
        "apply_deadline": "2026-07-20",
        "start_date": "2026-08-28",
        "end_date": "2026-12-16",
        "hours_per_week": "20 hours per week",
        "recruiter": "Not provided",
        "recruiter_email": "Not provided",
        "company_size": "Not specified",
        "industry": "Professional Services",
        "employer_description": (
            "A fictional employer used only for system testing."
        ),
    },
    {
        "source": "Fake Worker",
        "source_tier": 0,
        "company_name": "Immediate Start Manufacturing",
        "title": "HR Operations Intern",
        "location_raw": "Philadelphia, PA",
        "city": "Philadelphia",
        "state": "PA",
        "country": "US",
        "remote_type": "Onsite",
        "employment_type": "Internship",
        "job_url": "https://example.com/jobs/hr-operations",
        "apply_url": "https://example.com/apply/hr-operations",
        "description_raw": (
            "Urgently hiring an HR intern who must be available "
            "to start immediately and support employee files, "
            "onboarding, and HR administration."
        ),
        "responsibilities": (
            "Maintain employee files, support onboarding, "
            "and complete administrative HR tasks."
        ),
        "qualifications": (
            "Strong organization, communication, "
            "and Microsoft Office skills."
        ),
        "preferred_skills": "Excel, HRIS, onboarding",
        "work_authorization": "Not specified",
        "benefits": "Not specified",
        "salary_raw": "Not specified",
        "normalized_hourly_min": None,
        "normalized_hourly_max": None,
        "salary_confidence": "unknown",
        "target_track": "HR Operations",
        "hunter_score": 84,
        "match_label": "GOOD MATCH - CPT REVIEW",
        "status": "found",
        "cpt_trapdoor": 1,
        "ghost_risk_score": 0,
        "date_posted": "2026-07-02",
        "apply_deadline": "Not specified",
        "start_date": "Immediate",
        "end_date": "Not specified",
        "hours_per_week": "20 hours per week",
        "recruiter": "Not provided",
        "recruiter_email": "Not provided",
        "company_size": "Not specified",
        "industry": "Manufacturing",
        "employer_description": (
            "A fictional employer used only for CPT-rule testing."
        ),
    },
]


def save_fake_job(
    connection,
    job: dict[str, Any],
) -> tuple[int, bool, str]:
    job.update(score_job(job))
    job_fingerprint = create_job_fingerprint(job)
    manual_job_text = build_manual_job_text(job)

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO jobs (
            job_fingerprint,
            source,
            source_tier,
            company_name,
            title,
            location_raw,
            city,
            state,
            country,
            remote_type,
            job_url,
            apply_url,
            description_raw,
            salary_raw,
            normalized_hourly_min,
            normalized_hourly_max,
            salary_confidence,
            target_track,
            hunter_score,
            match_label,
            status,
            hard_rejection_reason,
            cpt_trapdoor,
            ghost_risk_score,
            score_breakdown_json,
            scoring_version,
            last_scored_at,
            date_posted,
            apply_deadline,
            start_date,
            end_date,
            manual_job_text
        )
        VALUES (
            :job_fingerprint,
            :source,
            :source_tier,
            :company_name,
            :title,
            :location_raw,
            :city,
            :state,
            :country,
            :remote_type,
            :job_url,
            :apply_url,
            :description_raw,
            :salary_raw,
            :normalized_hourly_min,
            :normalized_hourly_max,
            :salary_confidence,
            :target_track,
            :hunter_score,
            :match_label,
            :status,
            :hard_rejection_reason,
            :cpt_trapdoor,
            :ghost_risk_score,
            :score_breakdown_json,
            :scoring_version,
            :last_scored_at,
            :date_posted,
            :apply_deadline,
            :start_date,
            :end_date,
            :manual_job_text
        )
        """,
        {
            **job,
            "job_fingerprint": job_fingerprint,
            "manual_job_text": manual_job_text,
        },
    )

    inserted = cursor.rowcount == 1

    row = connection.execute(
        """
        SELECT id
        FROM jobs
        WHERE job_fingerprint = ?
        """,
        (job_fingerprint,),
    ).fetchone()

    job_id = int(row["id"])

    if not inserted:
        connection.execute(
            """
            UPDATE jobs
            SET
                target_track = :target_track,
                hunter_score = :hunter_score,
                match_label = :match_label,
                status = :status,
                hard_rejection_reason =
                    :hard_rejection_reason,
                cpt_trapdoor = :cpt_trapdoor,
                ghost_risk_score =
                    :ghost_risk_score,
                score_breakdown_json =
                    :score_breakdown_json,
                scoring_version =
                    :scoring_version,
                last_scored_at =
                    :last_scored_at,
                last_seen_at =
                    CURRENT_TIMESTAMP,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = :job_id
            """,
            {
                **job,
                "job_id": job_id,
            },
        )

    event_type = (
        "fake_job_created"
        if inserted
        else "fake_job_duplicate"
    )

    connection.execute(
        """
        INSERT INTO events (
            job_id,
            event_type,
            actor,
            event_status,
            payload_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            job_id,
            event_type,
            "fake_worker",
            "recorded",
            json.dumps(
                {
                    "company": job["company_name"],
                    "title": job["title"],
                    "hunter_score": job["hunter_score"],
                    "job_fingerprint": job_fingerprint,
                    "created_at": utc_now(),
                }
            ),
        ),
    )

    return job_id, inserted, job_fingerprint


def main() -> None:
    initialize_database()

    inserted_count = 0
    duplicate_count = 0

    connection = get_connection()

    try:
        for fake_job in FAKE_JOBS:
            job_id, inserted, fingerprint = save_fake_job(
                connection,
                fake_job,
            )

            if inserted:
                inserted_count += 1
                result = "INSERTED"
            else:
                duplicate_count += 1
                result = "DUPLICATE"

            print(
                f"{result}: job_id={job_id} | "
                f"{fake_job['company_name']} | "
                f"{fake_job['title']} | "
                f"score={fake_job['hunter_score']} | "
                f"fingerprint={fingerprint[:12]}..."
            )

        connection.commit()
    finally:
        connection.close()

    print()
    print(f"Fake jobs inserted: {inserted_count}")
    print(f"Duplicates detected: {duplicate_count}")
    print("Telegram calls made: 0")
    print("n8n calls made: 0")
    print("Paid API calls made: 0")
    print("Fake worker test: OK")


if __name__ == "__main__":
    main()
