from __future__ import annotations

import importlib
import json
from uuid import uuid4

import pytest

from app import career_policy, database
from app import opportunity_intelligence_v2 as opportunity_v2
from app import phase45_truth_binding
from app import relationship_intelligence as relationship_v1
from app import relationship_intelligence_v2 as relationship_v2
from app.phase67_common import safe_owned_job_snapshot
from app.tenant_foundation import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    associate_owned_record,
    owner_context,
)


def _enable_phase67(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUNSHI_CAREER_POLICY_ENABLED", "1")
    monkeypatch.setenv("MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED", "1")


def _candidate_snapshot(*, revision: int = 1, digest_seed: str = "a") -> dict:
    return {
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "source_extraction_id": "phase67-extraction",
        "profile_revision": revision,
        "profile_digest": digest_seed * 64,
        "facts": [
            {
                "key": "skills.analytics.skills",
                "value": ["Excel", "Power BI", "People Analytics"],
                "protected": False,
            },
            {
                "key": "experience.0.bullets",
                "value": [
                    "Built workforce dashboards using Excel and Power BI.",
                    "Analyzed people data and HR reporting metrics.",
                ],
                "protected": False,
            },
            {
                "key": "application_defaults.visa_or_permit",
                "value": "SECRET-PROTECTED-VISA-VALUE",
                "protected": True,
            },
        ],
    }


def _job(
    *,
    company: str = "Analytics Co",
    title: str = "People Analytics Analyst",
    target_track: str = "People Analytics Analyst",
    location: str = "New York, NY",
    remote_type: str = "Hybrid",
    employment_type: str = "Full-time",
    skills_keywords: str = "Excel,Power BI,People Analytics",
    work_authorization: str | None = None,
    hourly_max: float | None = 50.0,
) -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,location_raw,city,state,country,
                remote_type,description_raw,target_track,employment_type,responsibilities,
                qualifications,preferred_qualifications,preferred_skills,skills_keywords,
                work_authorization,normalized_hourly_max,hunter_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"phase67-{uuid4()}",
                "fixture",
                company,
                title,
                location,
                "New York",
                "NY",
                "US",
                remote_type,
                "Build workforce dashboards and analyze people data using Excel and Power BI.",
                target_track,
                employment_type,
                "Build dashboards and analyze workforce data.",
                "Excel, Power BI, HR analytics.",
                "People analytics preferred.",
                "Workforce reporting and stakeholder support.",
                skills_keywords,
                work_authorization,
                hourly_max,
                95,
            ),
        ).lastrowid
        associate_owned_record(connection, record_domain="job", record_key=str(job_id))
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _bind_snapshot(monkeypatch: pytest.MonkeyPatch, box: list[dict]) -> None:
    monkeypatch.setattr(
        phase45_truth_binding,
        "current_candidate_profile_snapshot",
        lambda: box[0],
    )


def _configure_pass_policy() -> None:
    career_policy.save_preferences(
        {
            "target_roles": ["People Analytics Analyst"],
            "allowed_locations": ["New York"],
            "remote_allowed": True,
            "hybrid_allowed": True,
            "onsite_allowed": True,
        }
    )
    career_policy.save_autopilot_policy(
        {
            "allowed_role_families": ["People Analytics Analyst"],
            "allowed_locations": ["New York"],
            "employment_types": ["Full-time"],
        }
    )


def _strong_contact(job_id: int, *, name: str = "Strong Contact", relevance: str = "Relevant") -> str:
    contact_id = relationship_v1.save_contact(
        {
            "display_name": name,
            "company_name": "Analytics Co",
            "title": "People Analytics Director",
            "contact_type": "mutual_connection",
            "relevance": relevance,
            "confidence": 1.0,
            "source": "user_supplied",
            "recommended_action": "connect",
            "email_provenance": "not_provided",
        }
    )
    relationship_v1.add_evidence(
        contact_id=contact_id,
        source="user_supplied",
        evidence_summary="Candidate-confirmed direct professional relationship.",
        confidence=1.0,
    )
    relationship_v1.link_contact_to_job(contact_id=contact_id, job_id=job_id)
    return contact_id


def test_phase67_features_remain_disabled_by_default(hunter_db) -> None:
    assert opportunity_v2.opportunity_intelligence_enabled() is False
    assert relationship_v2.relationship_intelligence_enabled() is False


def test_owned_job_snapshot_is_deterministic_and_tenant_isolated(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _job()
    first = safe_owned_job_snapshot(job_id)
    second = safe_owned_job_snapshot(job_id)
    assert first["job_snapshot_sha256"] == second["job_snapshot_sha256"]

    connection = database.get_connection()
    try:
        connection.execute("INSERT INTO tenants(tenant_id,display_name) VALUES ('team-b','Team B')")
        connection.execute("INSERT INTO app_users(user_id,display_name) VALUES ('user-b','User B')")
        connection.execute(
            "INSERT INTO tenant_memberships(tenant_id,user_id,role) VALUES ('team-b','user-b','member')"
        )
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setenv("MUNSHI_TENANT_FOUNDATION_ENABLED", "1")
    with owner_context(tenant_id="team-b", user_id="user-b"):
        with pytest.raises(LookupError, match="not owned"):
            safe_owned_job_snapshot(job_id)


def test_phase6_missing_fit_evidence_is_unknown_not_zero(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    box = [
        {
            **_candidate_snapshot(),
            "facts": [
                {
                    "key": "application_defaults.visa_or_permit",
                    "value": "SECRET-PROTECTED-VISA-VALUE",
                    "protected": True,
                }
            ],
        }
    ]
    _bind_snapshot(monkeypatch, box)
    career_policy.save_preferences({})
    career_policy.save_autopilot_policy({})

    result = opportunity_v2.evaluate_job(_job(), persist=False)

    assert result["status"] == "NEEDS_INPUT"
    assert result["opportunity_score"] is None
    assert result["score_confidence"] == 0.0
    assert {
        "skill_fit",
        "evidence_backed_experience_fit",
        "career_direction_fit",
    } <= set(result["unknowns"])
    assert "SECRET-PROTECTED-VISA-VALUE" not in json.dumps(result)


def test_phase6_salary_and_work_authorization_remain_unresolved_without_safe_comparable_evidence(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    box = [_candidate_snapshot()]
    _bind_snapshot(monkeypatch, box)
    career_policy.save_preferences(
        {
            "target_roles": ["People Analytics Analyst"],
            "minimum_salary": 80000,
        }
    )
    career_policy.save_autopilot_policy({"hard_salary_floor": 85000})

    result = opportunity_v2.evaluate_job(
        _job(work_authorization="Must already be authorized to work in the United States", hourly_max=60),
        persist=False,
    )

    assert result["status"] == "NEEDS_INPUT"
    assert "comparable_salary_max" in result["unknowns"]
    assert "work_authorization_match" in result["unknowns"]
    assert not any("salary_below" in failure for failure in result["hard_failures"])
    assert "SECRET-PROTECTED-VISA-VALUE" not in json.dumps(result)


def test_phase6_persisted_evaluation_is_digest_bound_and_becomes_stale_when_truth_changes(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    box = [_candidate_snapshot()]
    _bind_snapshot(monkeypatch, box)
    _configure_pass_policy()
    job_id = _job()

    persisted = opportunity_v2.evaluate_job(job_id)
    loaded = opportunity_v2.get_evaluation(persisted["evaluation_id"])

    assert loaded["result_sha256"] == persisted["result_sha256"]
    assert loaded["job_snapshot_sha256"] == persisted["job_snapshot_sha256"]
    assert opportunity_v2.evaluation_freshness(persisted["evaluation_id"])["fresh"] is True

    box[0] = _candidate_snapshot(revision=2, digest_seed="b")
    freshness = opportunity_v2.evaluation_freshness(persisted["evaluation_id"])
    assert freshness["fresh"] is False
    assert "candidate_truth_binding.profile_revision" in freshness["changed_bindings"]
    assert "candidate_truth_binding.profile_digest" in freshness["changed_bindings"]


def test_phase7_relationship_evidence_cannot_override_phase6_hard_policy_failure(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    box = [_candidate_snapshot()]
    _bind_snapshot(monkeypatch, box)
    career_policy.save_preferences({"target_roles": ["People Analytics Analyst"]})
    career_policy.save_autopilot_policy({"company_exclusions": ["Analytics Co"]})
    job_id = _job()
    evaluation = opportunity_v2.evaluate_job(job_id)
    _strong_contact(job_id)

    strategy = relationship_v2.strategy_for_job(
        job_id,
        opportunity_evaluation_id=evaluation["evaluation_id"],
    )

    assert evaluation["status"] == "FAIL"
    assert evaluation["pursuit_strategy"]["pursuit_state"] == "IGNORE"
    assert strategy["ranked_contacts"][0]["relationship_score"] == 100.0
    assert strategy["strategy"]["combined_pursuit_state"] == "IGNORE"
    assert strategy["strategy"]["networking_action"] == "no_action"
    assert "cannot override" in strategy["strategy"]["reason"]
    assert strategy["automatic_outreach_executed"] is False


def test_phase7_free_text_relevance_and_email_availability_do_not_change_relationship_score(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    job_id = _job()

    first = _strong_contact(job_id, name="Alpha Contact", relevance="Caller says perfect fit")
    second = relationship_v1.save_contact(
        {
            "display_name": "Beta Contact",
            "company_name": "Analytics Co",
            "title": "People Analytics Director",
            "contact_type": "mutual_connection",
            "relevance": "Caller says weak fit",
            "confidence": 1.0,
            "source": "user_supplied",
            "recommended_action": "connect",
            "email": "beta@example.test",
            "email_provenance": "explicit_contact_email",
        }
    )
    relationship_v1.add_evidence(
        contact_id=second,
        source="user_supplied",
        evidence_summary="Candidate-confirmed direct professional relationship.",
        confidence=1.0,
    )
    relationship_v1.link_contact_to_job(contact_id=second, job_id=job_id)

    result = relationship_v2.strategy_for_job(job_id, persist=False)
    by_id = {item["contact_id"]: item for item in result["ranked_contacts"]}

    assert by_id[first]["relationship_score"] == by_id[second]["relationship_score"] == 100.0
    assert by_id[first]["contact_information_state"]["email"] == "unknown_unverified"
    assert by_id[second]["contact_information_state"]["email"] == "known_contact_email"
    for contact in by_id.values():
        component_names = {item["input"] for item in contact["score_explanation"]}
        assert "relevance" not in component_names
        assert "email" not in component_names


def test_phase7_missing_relationship_evidence_is_unknown_and_requires_review(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_phase67(monkeypatch)
    job_id = _job()
    contact_id = relationship_v1.save_contact(
        {
            "display_name": "Evidence Pending",
            "company_name": "Analytics Co",
            "title": "Recruiter",
            "contact_type": "recruiter",
            "relevance": "Potentially relevant",
            "confidence": 0.9,
            "source": "public_profile",
            "recommended_action": "review",
            "email_provenance": "not_provided",
        }
    )
    relationship_v1.link_contact_to_job(contact_id=contact_id, job_id=job_id)

    result = relationship_v2.strategy_for_job(job_id, persist=False)
    contact = result["ranked_contacts"][0]

    assert "relationship_evidence_strength" in contact["unknowns"]
    assert contact["recommended_action"] == "review"
    assert contact["automatic_outreach_executed"] is False


def test_migration_032_is_additive_and_preserves_canonical_jobs_table(hunter_db) -> None:
    job_id = _job()
    connection = database.get_connection()
    try:
        importlib.import_module("migrations.032_phase67_intelligence_snapshots").apply(connection)
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        job = connection.execute("SELECT id,title FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        connection.close()

    assert {
        "jobs",
        "opportunity_intelligence_evaluations",
        "relationship_strategy_snapshots",
    } <= tables
    assert dict(job) == {"id": job_id, "title": "People Analytics Analyst"}
