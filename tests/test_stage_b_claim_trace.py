from __future__ import annotations

import copy

import pytest

from app import database
from app import jd_claim_trace_v1 as trace
from app import jd_resume_plan_v1 as planner
from app.phase67_common import sha256_json
from app.tenant_foundation import DEFAULT_TENANT_ID, DEFAULT_USER_ID


def _plan() -> dict:
    payload = {
        "contract_version": planner.PLAN_VERSION,
        "authority": "munshi-hr-hunter",
        "tenant_id": DEFAULT_TENANT_ID,
        "user_id": DEFAULT_USER_ID,
        "job_id": 77,
        "job_snapshot_sha256": "a" * 64,
        "jd_snapshot_id": "jd-intel-example",
        "jd_snapshot_digest": "b" * 64,
        "match_snapshot_id": "candidate-job-match-example",
        "match_digest": "c" * 64,
        "candidate_truth_binding": {
            "source_extraction_id": "extract-example",
            "profile_revision": 3,
            "profile_digest": "d" * 64,
        },
        "requirement_refs": [
            {
                "requirement_id": "JDREQ-001",
                "type": "RESPONSIBILITY",
                "priority": "CORE_RESPONSIBILITY",
                "exact_text": "Build workforce dashboards using Excel and Power BI.",
                "source_field": "responsibilities",
                "match_status": "DIRECT",
                "match_score": 1.0,
                "evidence_ids": ["truth:fact-dashboard"],
                "evidence_keys": ["experience.0.bullets"],
            },
            {
                "requirement_id": "JDREQ-002",
                "type": "TOOL",
                "priority": "MUST_HAVE",
                "exact_text": "Advanced Excel required.",
                "source_field": "qualifications",
                "match_status": "DIRECT",
                "match_score": 1.0,
                "evidence_ids": ["truth:fact-skills"],
                "evidence_keys": ["skills.analytics.skills"],
            },
            {
                "requirement_id": "JDREQ-003",
                "type": "EXPERIENCE",
                "priority": "MUST_HAVE",
                "exact_text": "10+ years of payroll implementation experience required.",
                "source_field": "qualifications",
                "match_status": "NO_EVIDENCE",
                "match_score": 0.0,
                "evidence_ids": [],
                "evidence_keys": [],
            },
            {
                "requirement_id": "JDREQ-004",
                "type": "WORK_AUTHORIZATION",
                "priority": "MUST_HAVE",
                "exact_text": "Must be authorized to work in the United States.",
                "source_field": "work_authorization",
                "match_status": "UNKNOWN",
                "match_score": None,
                "evidence_ids": [],
                "evidence_keys": [],
            },
        ],
        "summary_priority_requirement_ids": ["JDREQ-001", "JDREQ-002"],
        "skills_priority_requirement_ids": ["JDREQ-002"],
        "experience_priority_requirement_ids": ["JDREQ-001", "JDREQ-002"],
        "preferred_requirement_ids": [],
        "do_not_claim_requirement_ids": ["JDREQ-003", "JDREQ-004"],
        "unsupported_must_have_requirement_ids": ["JDREQ-003"],
        "supported_jd_terms": ["Excel", "Power BI"],
        "one_page_retention_order": ["JDREQ-001", "JDREQ-002"],
        "diagnostics": {
            "supported_requirement_count": 2,
            "do_not_claim_requirement_count": 2,
            "evidence_coverage_score": 75.0,
            "score_confidence": 0.8,
            "automatic_actions_executed": False,
        },
        "mutation_authority": False,
        "submission_authority": False,
    }
    digest = sha256_json(payload)
    return {
        **payload,
        "plan_id": f"jd-resume-plan-{digest[:24]}",
        "plan_digest": digest,
        "generated_at": "2026-09-06T00:00:00+00:00",
    }


def _resume() -> dict:
    return {
        "version_id": "native-resume-example",
        "job_id": 77,
        "html_sha256": "e" * 64,
        "candidate_truth_bound": True,
        "candidate_truth_binding": {
            "source_extraction_id": "extract-example",
            "profile_revision": 3,
            "profile_digest": "d" * 64,
            "source_profile_sha256": "f" * 64,
            "source_resume_sha256": "1" * 64,
        },
        "job_snapshot_bound": True,
        "job_snapshot_binding": {
            "job_id": 77,
            "job_snapshot_sha256": "a" * 64,
            "generation_input_sha256": "2" * 64,
        },
        "document": {
            "schema_version": "native-resume-v1",
            "template_version": "ats-single-column-v1",
            "candidate_name": "Example Candidate",
            "contact": {
                "location": "New Jersey",
                "email": "candidate@example.test",
                "phone": "",
                "linkedin": "",
                "github": "",
                "portfolio": "",
            },
            "summary": {
                "text": "People analytics professional with Excel and Power BI experience.",
                "evidence_ids": ["truth:fact-skills"],
            },
            "education": [],
            "skills": [
                {
                    "label": "Analytics",
                    "skills": ["Excel", "Power BI"],
                    "evidence_ids": ["truth:fact-skills"],
                }
            ],
            "experience": [
                {
                    "organization": "Example Co",
                    "title": "HR Analyst",
                    "dates": "",
                    "location": "",
                    "bullets": [
                        {
                            "text": "Built workforce dashboards using Excel and Power BI.",
                            "evidence_ids": ["truth:fact-dashboard"],
                        },
                        {
                            "text": "Maintained accurate employee documentation.",
                            "evidence_ids": ["truth:fact-documentation"],
                        },
                    ],
                }
            ],
            "projects": [],
            "certifications": [],
        },
    }


def test_stage_b_claim_trace_links_resume_evidence_to_exact_jd_requirements() -> None:
    result = trace.build_trace(plan=_plan(), resume=_resume())
    claims = {item["claim_id"]: item for item in result["claims"]}

    assert "JDREQ-002" in claims["CLAIM-SUMMARY-001"]["requirement_ids"]
    assert claims["CLAIM-SUMMARY-001"]["support_status"] == "EVIDENCE_SUPPORTED_JD_LINKED"
    assert "JDREQ-001" in claims["CLAIM-EXP-001-BULLET-001"]["requirement_ids"]
    assert claims["CLAIM-EXP-001-BULLET-002"]["support_status"] == "EVIDENCE_SUPPORTED_UNLINKED"
    assert result["submission_authority"] is False
    assert result["diagnostics"]["visible_resume_changed"] is False


def test_stage_b_claim_trace_never_links_do_not_claim_requirements() -> None:
    result = trace.build_trace(plan=_plan(), resume=_resume())

    assert "JDREQ-003" not in result["linked_requirement_ids"]
    assert "JDREQ-004" not in result["linked_requirement_ids"]
    assert {"JDREQ-003", "JDREQ-004"} <= set(result["do_not_claim_requirement_ids"])
    for claim in result["claims"]:
        assert not {"JDREQ-003", "JDREQ-004"}.intersection(claim["requirement_ids"])


def test_stage_b_claim_trace_fails_closed_on_job_or_candidate_truth_mismatch() -> None:
    job_changed = _resume()
    job_changed["job_snapshot_binding"]["job_snapshot_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="different job snapshots"):
        trace.build_trace(plan=_plan(), resume=job_changed)

    truth_changed = _resume()
    truth_changed["candidate_truth_binding"]["profile_revision"] = 4
    with pytest.raises(ValueError, match="different Candidate Truth states"):
        trace.build_trace(plan=_plan(), resume=truth_changed)


def test_stage_b_trace_resume_orchestration_stays_inert_and_nonpersistent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    resume = _resume()
    monkeypatch.setattr(planner, "get_plan", lambda _plan_id: plan)
    monkeypatch.setattr(planner, "plan_freshness", lambda _plan_id: {"fresh": True})
    monkeypatch.setattr(trace.resume_v4, "get_version", lambda _version_id: resume)

    result = trace.trace_resume(
        plan_id=plan["plan_id"],
        resume_version_id=resume["version_id"],
        persist=False,
    )

    assert result["plan_id"] == plan["plan_id"]
    assert result["resume_version_id"] == resume["version_id"]
    assert result["diagnostics"]["automatic_actions_executed"] is False


def test_stage_b_claim_trace_validation_rejects_forbidden_requirement_links() -> None:
    result = trace.build_trace(plan=_plan(), resume=_resume())
    broken = copy.deepcopy(result)
    broken["claims"][0]["requirement_ids"].append("JDREQ-003")
    broken.pop("trace_digest", None)

    with pytest.raises(ValueError, match="do-not-claim"):
        trace.validate_trace(broken)


def test_stage_b_claim_trace_migration_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        before = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        migration = __import__(
            "migrations.038_stage_b_jd_claim_trace_v1",
            fromlist=["apply"],
        )
        migration.apply(connection)
        connection.commit()
        after = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        connection.close()

    assert before <= after
    assert "jd_resume_claim_traces" in after
