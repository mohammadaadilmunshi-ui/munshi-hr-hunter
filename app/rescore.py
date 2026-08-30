from __future__ import annotations

import json
from typing import Any

from app.database import (
    get_connection,
    initialize_database,
)
from app.scoring import score_job


PRESERVED_WORKFLOW_STATUSES = {
    "held",
    "approved_for_n8n",
    "already_applied",
    "rejected_similar",
    "processing",
    "application_ready",
    "n8n_failed",
    "retry_needed",
}


MANUAL_FIELD_MAP = {
    "Job Title": "title",
    "Company Name": "company_name",
    "Location": "location_raw",
    "Remote / Hybrid / Onsite": "remote_type",
    "Pay / Wage / Salary": "salary_raw",
    "Posted Date": "date_posted",
    "Apply Deadline": "apply_deadline",
    "Start Date": "start_date",
    "End Date": "end_date",
    "Job Description": "description_raw",
    "Responsibilities": "responsibilities",
    "Qualifications": "qualifications",
    "Preferred Skills": "preferred_skills",
    "Work Authorization / Sponsorship":
        "work_authorization",
    "Application Link": "apply_url",
}


def parse_manual_job_text(
    manual_text: str,
) -> dict[str, str]:
    """
    Extract field values from manual_job_text without including
    labels such as 'Recruiter / Contact' in scoring text.
    """
    parsed: dict[str, str] = {}

    current_label: str | None = None
    current_lines: list[str] = []

    def save_current_field() -> None:
        if current_label is None:
            return

        value = "\n".join(current_lines).strip()

        if value.casefold() in {
            "",
            "not specified",
            "not provided",
        }:
            return

        field_name = MANUAL_FIELD_MAP[current_label]
        parsed[field_name] = value

    for line in str(manual_text or "").splitlines():
        stripped = line.strip()

        possible_label = (
            stripped[:-1]
            if stripped.endswith(":")
            else None
        )

        if possible_label in MANUAL_FIELD_MAP:
            save_current_field()
            current_label = possible_label
            current_lines = []
            continue

        if current_label is not None:
            current_lines.append(line)

    save_current_field()
    return parsed


def prepare_job_for_scoring(
    row: dict[str, Any],
) -> dict[str, Any]:
    job = dict(row)

    parsed_fields = parse_manual_job_text(
        str(job.get("manual_job_text") or "")
    )

    for field_name, field_value in parsed_fields.items():
        existing_value = job.get(field_name)

        if existing_value in {
            None,
            "",
            "Not specified",
            "Not provided",
        }:
            job[field_name] = field_value

    return job


def resolve_workflow_status(
    current_status: str,
    scoring_result: dict[str, Any],
) -> str:
    rejection_reason = scoring_result.get(
        "hard_rejection_reason"
    )

    if rejection_reason == "Company is blacklisted":
        return "blacklisted"

    if rejection_reason:
        return "rejected"

    if current_status in PRESERVED_WORKFLOW_STATUSES:
        return current_status

    return "found"


def rescore_all_jobs(
    actor: str = "system",
) -> dict[str, Any]:
    initialize_database()
    connection = get_connection()

    processed = 0
    changed = 0
    rejected = 0
    errors: list[dict[str, Any]] = []

    try:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            ORDER BY id
            """
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            job = prepare_job_for_scoring(row_dict)

            try:
                result = score_job(job)

                new_status = resolve_workflow_status(
                    str(row["status"] or "found"),
                    result,
                )

                if new_status in {
                    "rejected",
                    "blacklisted",
                }:
                    rejected += 1

                old_values = (
                    row["hunter_score"],
                    row["match_label"],
                    row["target_track"],
                    row["status"],
                    row["cpt_trapdoor"],
                    row["hard_rejection_reason"],
                )

                new_values = (
                    result["hunter_score"],
                    result["match_label"],
                    result["target_track"],
                    new_status,
                    result["cpt_trapdoor"],
                    result["hard_rejection_reason"],
                )

                if old_values != new_values:
                    changed += 1

                connection.execute(
                    """
                    UPDATE jobs
                    SET
                        target_track = ?,
                        hunter_score = ?,
                        match_label = ?,
                        status = ?,
                        hard_rejection_reason = ?,
                        cpt_trapdoor = ?,
                        ghost_risk_score = ?,
                        score_breakdown_json = ?,
                        scoring_version = ?,
                        last_scored_at = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        result["target_track"],
                        result["hunter_score"],
                        result["match_label"],
                        new_status,
                        result["hard_rejection_reason"],
                        result["cpt_trapdoor"],
                        result["ghost_risk_score"],
                        result["score_breakdown_json"],
                        result["scoring_version"],
                        result["last_scored_at"],
                        row["id"],
                    ),
                )

                processed += 1

            except Exception as error:
                errors.append(
                    {
                        "job_id": row["id"],
                        "company_name":
                            row["company_name"],
                        "error": str(error),
                    }
                )

        summary = {
            "processed": processed,
            "changed": changed,
            "rejected": rejected,
            "errors": errors,
            "external_calls_made": 0,
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
                'jobs_rescored',
                ?,
                ?,
                ?
            )
            """,
            (
                actor,
                (
                    "completed"
                    if not errors
                    else "completed_with_errors"
                ),
                json.dumps(
                    summary,
                    ensure_ascii=False,
                ),
            ),
        )

        connection.commit()
        return summary

    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    result = rescore_all_jobs(
        actor="rescore_cli"
    )

    print("Jobs processed:", result["processed"])
    print("Jobs changed:", result["changed"])
    print("Jobs rejected:", result["rejected"])
    print("Errors:", len(result["errors"]))
    print("External calls made: 0")
    print("Re-scoring: OK")


if __name__ == "__main__":
    main()
