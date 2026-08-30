from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.job_detail import (
    build_manual_job_text,
    enrich_job_details,
    serialize_job_details,
)
from app.scoring import score_job
from app.universal_dedupe import (
    choose_preferred_url,
    create_universal_job_fingerprint,
    disambiguated_fingerprint,
    duplicate_evidence,
    find_semantic_duplicate,
)
from app.job_quality import apply_quality_gate
from app.dedupe_policy import dedupe_keeper_allowed


PROTECTED_STATUSES = {
    "hold",
    "held",
    "rejected",
    "already_applied",
    "approved_for_n8n",
    "sent_to_n8n",
    "application_ready",
    "n8n_failed",
}


def canonical_url(value: Any) -> str:
    raw_url = str(value or "").strip()

    if not raw_url:
        return ""

    try:
        parts = urlsplit(raw_url)

        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                "",
                "",
            )
        )
    except ValueError:
        return raw_url.lower()


def create_url_fingerprint(job: dict[str, Any]) -> str | None:
    url = canonical_url(
        job.get("apply_url")
        or job.get("job_url")
    )

    if not url:
        return None

    return hashlib.sha256(
        url.encode("utf-8")
    ).hexdigest()


def prepare_job(job: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(job)

    defaults: dict[str, Any] = {
        "source": "Unknown",
        "source_tier": 99,
        "ats_job_id": None,
        "company_name": "Unknown Company",
        "title": "Unknown Position",
        "location_raw": "Not specified",
        "city": None,
        "state": None,
        "country": None,
        "remote_type": "Not specified",
        "employment_type": "Not specified",
        "job_url": None,
        "apply_url": None,
        "description_raw": "Not specified",
        "salary_raw": None,
        "normalized_hourly_min": None,
        "normalized_hourly_max": None,
        "salary_confidence": "unknown",
        "date_posted": None,
        "apply_deadline": None,
        "start_date": None,
        "end_date": None,
        "hours_per_week": None,
        "responsibilities": None,
        "qualifications": None,
        "preferred_qualifications": None,
        "preferred_skills": None,
        "skills_keywords": None,
        "work_authorization": None,
        "benefits": None,
        "recruiter": None,
        "recruiter_email": None,
        "company_size": None,
        "industry": None,
        "employer_description": None,
        "detail_extraction_status": None,
        "detail_extraction_version": None,
        "detail_extraction_json": None,
    }

    for key, value in defaults.items():
        prepared.setdefault(key, value)

    prepared = enrich_job_details(prepared)

    prepared.update(
        score_job(prepared)
    )

    canonical_gate = prepared.get("_targeting_decision")
    canonical_accepted = bool(
        isinstance(canonical_gate, dict)
        and canonical_gate.get("canonical_targeting_gate")
        and canonical_gate.get("accepted")
    )

    # Canonical discovery eligibility is final. The legacy quality gate is
    # retained only for explicitly exempt/non-discovery paths.
    quality_overrides = {} if canonical_accepted else apply_quality_gate(prepared)
    if quality_overrides:
        prepared.update(quality_overrides)

    prepared["job_fingerprint"] = (
        create_universal_job_fingerprint(prepared)
    )
    prepared["url_fingerprint"] = (
        create_url_fingerprint(prepared)
    )
    prepared["manual_job_text"] = (
        build_manual_job_text(prepared)
    )

    return prepared


def find_existing_job(
    connection,
    job: dict[str, Any],
):
    # A historical row is a duplicate keeper only if it is still authoritative
    # under the current dashboard targeting policy, or is explicitly protected
    # by manual/application state. This preserves audit history without letting
    # polluted rows suppress corrected rediscovery.
    try:
        from app.dashboard_targeting_gate import load_dashboard_targeting_rules

        dedupe_rules = load_dashboard_targeting_rules()
    except Exception:
        dedupe_rules = None

    ignored_keeper_ids: list[int] = []

    row = connection.execute(
        """
        SELECT *
        FROM jobs
        WHERE job_fingerprint = ?
        LIMIT 1
        """,
        (job["job_fingerprint"],),
    ).fetchone()

    if row is not None:
        candidate = dict(row)
        if dedupe_keeper_allowed(candidate, rules=dedupe_rules):
            evidence = duplicate_evidence(
                candidate,
                job,
            )

            if evidence is not None:
                job[
                    "_duplicate_evidence"
                ] = evidence

                return (
                    row,
                    "job_fingerprint",
                )
        else:
            try:
                ignored_keeper_ids.append(int(candidate.get("id")))
            except (TypeError, ValueError):
                pass

        # Either the fingerprint collision was not semantically the same job or
        # the historical row is no longer a valid keeper. Use a deterministic
        # distinct fingerprint so the corrected job can be stored alongside the
        # preserved historical row.
        job["job_fingerprint"] = (
            disambiguated_fingerprint(job)
        )

    if job.get("url_fingerprint"):
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE url_fingerprint = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (job["url_fingerprint"],),
        ).fetchone()

        if row is not None:
            candidate = dict(row)
            if dedupe_keeper_allowed(candidate, rules=dedupe_rules):
                return row, "url_fingerprint"
            try:
                ignored_keeper_ids.append(int(candidate.get("id")))
            except (TypeError, ValueError):
                pass

    if job.get("ats_job_id"):
        row = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE
                lower(source) = lower(?)
                AND ats_job_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                job["source"],
                job["ats_job_id"],
            ),
        ).fetchone()

        if row is not None:
            candidate = dict(row)
            if dedupe_keeper_allowed(candidate, rules=dedupe_rules):
                return row, "source_ats_job_id"
            try:
                ignored_keeper_ids.append(int(candidate.get("id")))
            except (TypeError, ValueError):
                pass

    semantic_result = (
        find_semantic_duplicate(
            connection,
            job,
        )
    )

    if semantic_result is not None:
        row, evidence = semantic_result

        job[
            "_duplicate_evidence"
        ] = evidence

        return (
            row,
            "canonical_semantic_duplicate",
        )

    if ignored_keeper_ids:
        job["_ignored_historical_dedupe_keeper_ids"] = sorted(set(ignored_keeper_ids))

    return None, None


def save_job(
    connection,
    raw_job: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    job = prepare_job(raw_job)

    existing, duplicate_reason = (
        find_existing_job(
            connection,
            job,
        )
    )

    if existing is None:
        cursor = connection.execute(
            """
            INSERT INTO jobs (
                job_fingerprint,
                url_fingerprint,
                ats_job_id,
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
                date_posted,
                apply_deadline,
                start_date,
                end_date,
                manual_job_text,
                employment_type,
                hours_per_week,
                responsibilities,
                qualifications,
                preferred_qualifications,
                preferred_skills,
                skills_keywords,
                work_authorization,
                benefits,
                company_size,
                industry,
                employer_description,
                detail_extraction_status,
                detail_extraction_version,
                detail_extraction_json,
                score_breakdown_json,
                scoring_version,
                last_scored_at
            )
            VALUES (
                :job_fingerprint,
                :url_fingerprint,
                :ats_job_id,
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
                :date_posted,
                :apply_deadline,
                :start_date,
                :end_date,
                :manual_job_text,
                :employment_type,
                :hours_per_week,
                :responsibilities,
                :qualifications,
                :preferred_qualifications,
                :preferred_skills,
                :skills_keywords,
                :work_authorization,
                :benefits,
                :company_size,
                :industry,
                :employer_description,
                :detail_extraction_status,
                :detail_extraction_version,
                :detail_extraction_json,
                :score_breakdown_json,
                :scoring_version,
                :last_scored_at
            )
            """,
            serialize_job_details(job),
        )

        job_id = int(cursor.lastrowid)
        inserted = True
        duplicate_reason = None

    else:
        job_id = int(existing["id"])
        existing_status = str(
            existing["status"] or ""
        ).strip().lower()

        resulting_status = (
            existing["status"]
            if existing_status
            in PROTECTED_STATUSES
            else job["status"]
        )

        # UNIVERSAL_DEDUPE_STORAGE_V2
        job["apply_url"] = (
            choose_preferred_url(
                existing["apply_url"],
                job.get("apply_url"),
            )
        )

        job["job_url"] = (
            choose_preferred_url(
                existing["job_url"],
                job.get("job_url"),
            )
        )

        # Preserve the richest stored detail when a duplicate source returns
        # only a title/snippet. Rebuild the n8n payload from the merged record.
        merged_detail = dict(existing)
        for key, value in job.items():
            if value not in (None, "", [], {}):
                merged_detail[key] = value
        existing_description = str(existing["description_raw"] or "")
        incoming_description = str(job.get("description_raw") or "")
        merged_detail["description_raw"] = (
            incoming_description
            if len(incoming_description) > len(existing_description)
            else existing_description
        )
        merged_detail = enrich_job_details(merged_detail)
        for detail_key in (
            "employment_type",
            "hours_per_week",
            "responsibilities",
            "qualifications",
            "preferred_qualifications",
            "preferred_skills",
            "skills_keywords",
            "work_authorization",
            "benefits",
            "company_size",
            "industry",
            "employer_description",
            "detail_extraction_status",
            "detail_extraction_version",
            "detail_extraction_json",
        ):
            job[detail_key] = merged_detail.get(detail_key)
        job["manual_job_text"] = build_manual_job_text(merged_detail)

        connection.execute(
            """
            UPDATE jobs
            SET
                url_fingerprint =
                    COALESCE(
                        url_fingerprint,
                        :url_fingerprint
                    ),
                ats_job_id =
                    COALESCE(
                        ats_job_id,
                        :ats_job_id
                    ),
                source =
                    CASE
                        WHEN
                            COALESCE(
                                :source_tier,
                                99
                            )
                            <
                            COALESCE(
                                source_tier,
                                99
                            )
                        THEN :source
                        ELSE source
                    END,
                source_tier =
                    CASE
                        WHEN
                            COALESCE(
                                :source_tier,
                                99
                            )
                            <
                            COALESCE(
                                source_tier,
                                99
                            )
                        THEN :source_tier
                        ELSE source_tier
                    END,
                company_name = :company_name,
                title = :title,
                location_raw = :location_raw,
                city = :city,
                state = :state,
                country = :country,
                remote_type = :remote_type,
                job_url =
                    COALESCE(
                        :job_url,
                        job_url
                    ),
                apply_url =
                    COALESCE(
                        :apply_url,
                        apply_url
                    ),
                description_raw =
                    CASE
                        WHEN
                            length(
                                COALESCE(
                                    :description_raw,
                                    ''
                                )
                            )
                            >
                            length(
                                COALESCE(
                                    description_raw,
                                    ''
                                )
                            )
                        THEN :description_raw
                        ELSE description_raw
                    END,
                salary_raw =
                    COALESCE(
                        :salary_raw,
                        salary_raw
                    ),
                normalized_hourly_min =
                    COALESCE(
                        :normalized_hourly_min,
                        normalized_hourly_min
                    ),
                normalized_hourly_max =
                    COALESCE(
                        :normalized_hourly_max,
                        normalized_hourly_max
                    ),
                salary_confidence =
                    COALESCE(
                        :salary_confidence,
                        salary_confidence
                    ),
                target_track = :target_track,
                hunter_score = :hunter_score,
                match_label = :match_label,
                status = :resulting_status,
                hard_rejection_reason =
                    :hard_rejection_reason,
                cpt_trapdoor = :cpt_trapdoor,
                ghost_risk_score =
                    :ghost_risk_score,
                date_posted =
                    COALESCE(
                        :date_posted,
                        date_posted
                    ),
                apply_deadline =
                    COALESCE(
                        :apply_deadline,
                        apply_deadline
                    ),
                start_date =
                    COALESCE(
                        :start_date,
                        start_date
                    ),
                end_date =
                    COALESCE(
                        :end_date,
                        end_date
                    ),
                manual_job_text =
                    :manual_job_text,
                employment_type = COALESCE(NULLIF(:employment_type, ''), employment_type),
                hours_per_week = COALESCE(NULLIF(:hours_per_week, ''), hours_per_week),
                responsibilities = COALESCE(NULLIF(:responsibilities, ''), responsibilities),
                qualifications = COALESCE(NULLIF(:qualifications, ''), qualifications),
                preferred_qualifications = COALESCE(NULLIF(:preferred_qualifications, ''), preferred_qualifications),
                preferred_skills = COALESCE(NULLIF(:preferred_skills, ''), preferred_skills),
                skills_keywords = COALESCE(NULLIF(:skills_keywords, ''), skills_keywords),
                work_authorization = COALESCE(NULLIF(:work_authorization, ''), work_authorization),
                benefits = COALESCE(NULLIF(:benefits, ''), benefits),
                company_size = COALESCE(NULLIF(:company_size, ''), company_size),
                industry = COALESCE(NULLIF(:industry, ''), industry),
                employer_description = COALESCE(NULLIF(:employer_description, ''), employer_description),
                detail_extraction_status = :detail_extraction_status,
                detail_extraction_version = :detail_extraction_version,
                detail_extraction_json = :detail_extraction_json,
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
                **serialize_job_details(job),
                "job_id": job_id,
                "resulting_status": (
                    resulting_status
                ),
            },
        )

        inserted = False

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
            (
                "real_job_created"
                if inserted
                else "real_job_duplicate"
            ),
            actor,
            "recorded",
            json.dumps(
                {
                    "source": job["source"],
                    "company": (
                        job["company_name"]
                    ),
                    "title": job["title"],
                    "hunter_score": (
                        job["hunter_score"]
                    ),
                    "job_fingerprint": (
                        job["job_fingerprint"]
                    ),
                    "duplicate_reason": (
                        duplicate_reason
                    ),
                },
                ensure_ascii=False,
            ),
        ),
    )

    return {
        "job_id": job_id,
        "inserted": inserted,
        "duplicate_reason": duplicate_reason,
        "company": job["company_name"],
        "title": job["title"],
        "hunter_score": job["hunter_score"],
        "match_label": job["match_label"],
        "status": (
            resulting_status
            if not inserted
            else job["status"]
        ),
    }

# AADIL_CANONICAL_JOB_STORE_V3
_storage_save_job = save_job


def _decision_run_id(job: dict[str, Any]) -> str:
    import uuid

    return str(job.get("_targeting_run_id") or uuid.uuid4())


def _record_targeting_decision(
    connection,
    *,
    raw_job: dict[str, Any],
    gate: dict[str, Any],
    primary_category: str,
    run_id: str,
) -> None:
    from app.targeting import within_run_identity

    evidence = {
        "reason": gate.get("canonical_reason") or gate.get("reason"),
        "role": gate.get("role_evidence"),
        "experience": gate.get("experience_evidence"),
        "hard_requirement": gate.get("hard_requirement_evidence"),
        "company": gate.get("company_evidence"),
        "location": gate.get("location_evidence"),
        "preference": gate.get("preference"),
    }
    connection.execute(
        """
        INSERT INTO targeting_decisions (
            run_id, source_name, external_id, job_identity, title,
            company_name, location_raw, primary_category,
            secondary_reasons_json, evidence_json, rules_version, rules_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, job_identity) DO UPDATE SET
            primary_category = excluded.primary_category,
            secondary_reasons_json = excluded.secondary_reasons_json,
            evidence_json = excluded.evidence_json,
            rules_version = excluded.rules_version,
            rules_hash = excluded.rules_hash,
            decided_at = CURRENT_TIMESTAMP
        """,
        (
            run_id,
            str(raw_job.get("source") or raw_job.get("source_name") or "Unknown")[:300],
            str(raw_job.get("external_id") or raw_job.get("ats_job_id") or raw_job.get("requisition_id") or "")[:300],
            within_run_identity(raw_job),
            str(raw_job.get("title") or raw_job.get("job_title") or "")[:500],
            str(raw_job.get("company_name") or raw_job.get("company") or "")[:500],
            str(raw_job.get("location_raw") or raw_job.get("location") or "")[:1000],
            primary_category,
            json.dumps(gate.get("secondary_reasons") or [], ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False, default=str),
            gate.get("rules_version"),
            gate.get("rules_hash") or gate.get("targeting_rules_hash"),
        ),
    )


def _persist_job_decision_metadata(
    connection,
    *,
    job_id: int,
    raw_job: dict[str, Any],
    gate: dict[str, Any],
) -> None:
    row = connection.execute(
        "SELECT job_fingerprint, source_provenance_json FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return
    try:
        provenance = json.loads(row["source_provenance_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        provenance = []
    if not isinstance(provenance, list):
        provenance = []
    item = {
        "source": str(raw_job.get("source") or raw_job.get("source_name") or "Unknown"),
        "external_id": str(raw_job.get("external_id") or raw_job.get("ats_job_id") or raw_job.get("requisition_id") or ""),
        "url": str(raw_job.get("apply_url") or raw_job.get("job_url") or raw_job.get("url") or ""),
    }
    if item not in provenance:
        provenance.append(item)
    evidence = {
        "reason": gate.get("canonical_reason") or gate.get("reason"),
        "role": gate.get("role_evidence"),
        "experience": gate.get("experience_evidence"),
        "hard_requirement": gate.get("hard_requirement_evidence"),
        "company": gate.get("company_evidence"),
        "location": gate.get("location_evidence"),
        "preference": gate.get("preference"),
    }
    connection.execute(
        """
        UPDATE jobs
        SET primary_decision = 'ELIGIBLE',
            secondary_reasons_json = ?,
            decision_evidence_json = ?,
            targeting_rules_version = ?,
            targeting_rules_hash = ?,
            role_evidence_json = ?,
            experience_evidence_json = ?,
            location_evidence_json = ?,
            duplicate_group = ?,
            source_provenance_json = ?,
            preference_score = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(gate.get("secondary_reasons") or [], ensure_ascii=False),
            json.dumps(evidence, ensure_ascii=False, default=str),
            gate.get("rules_version"),
            gate.get("rules_hash") or gate.get("targeting_rules_hash"),
            json.dumps(gate.get("role_evidence") or {}, ensure_ascii=False, default=str),
            json.dumps(gate.get("experience_evidence") or [], ensure_ascii=False, default=str),
            json.dumps(gate.get("location_evidence") or {}, ensure_ascii=False, default=str),
            str(row["job_fingerprint"] or ""),
            json.dumps(provenance, ensure_ascii=False, default=str),
            int((gate.get("preference") or {}).get("score") or 0),
            job_id,
        ),
    )


def save_job(
    connection,
    raw_job: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    """The single persistence boundary for canonical targeting and dedupe."""
    from app.dashboard_targeting_gate import adapter_gate_required, gate_adapter_job
    from app.targeting import PrimaryCategory

    job = dict(raw_job)
    gated = adapter_gate_required(job, actor)
    gate = job.get("_targeting_decision") if gated else None
    if not isinstance(gate, dict) or not gate.get("canonical_targeting_gate"):
        gate = gate_adapter_job(job, actor)
    run_id = _decision_run_id(job)

    if gated and not gate.get("accepted"):
        primary = str(gate.get("primary_category") or PrimaryCategory.REJECT_OTHER_TARGETING.value)
        _record_targeting_decision(
            connection,
            raw_job=job,
            gate=gate,
            primary_category=primary,
            run_id=run_id,
        )
        return {
            "job_id": None,
            "inserted": False,
            "duplicate_reason": None,
            "company": str(job.get("company_name") or job.get("company") or "Unknown Company"),
            "title": str(job.get("title") or job.get("job_title") or "Unknown Position"),
            "hunter_score": 0,
            "match_label": "Rejected by canonical targeting",
            "status": "rejected_by_dashboard_targeting",
            "primary_category": primary,
            "dashboard_rejection_reason": gate.get("reason"),
            "dashboard_location_rejection_reason": gate.get("location_rejection_reason"),
            "dashboard_gate": gate,
            "canonical_targeting_gate": True,
        }

    if gated:
        normalized = dict(gate.get("normalized_job") or job)
        for key, value in job.items():
            if str(key).startswith("_"):
                normalized[key] = value
        normalized["_targeting_run_id"] = run_id
        normalized.setdefault("entry_path", "adapter_discovery")
        job = normalized

    result = _storage_save_job(connection, job, actor=actor)
    result = dict(result)
    if gated:
        primary = (
            PrimaryCategory.ELIGIBLE.value
            if result.get("inserted")
            else PrimaryCategory.DUPLICATE.value
        )
        _record_targeting_decision(
            connection,
            raw_job=job,
            gate=gate,
            primary_category=primary,
            run_id=run_id,
        )
        if result.get("job_id") is not None:
            _persist_job_decision_metadata(
                connection,
                job_id=int(result["job_id"]),
                raw_job=job,
                gate=gate,
            )
        result["primary_category"] = primary
        result["canonical_targeting_gate"] = True

    try:
        from app.telegram_control_center import record_job_observation

        record_job_observation(result=result, raw_job=job, actor=actor)
    except Exception:
        # Observability may never make successful persistence unavailable.
        pass

    return result
# AADIL_CANONICAL_JOB_STORE_V3_END
