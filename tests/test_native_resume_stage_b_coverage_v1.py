from __future__ import annotations

from app import native_resume_service_v5 as resume_v5


def _document():
    return resume_v5.v4.v1.ResumeDocument.model_validate(
        {
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
                "text": "People analytics candidate with workforce reporting experience.",
                "evidence_ids": ["ev-core"],
            },
            "education": [],
            "skills": [
                {
                    "label": "Analytics",
                    "skills": ["Excel", "Power BI"],
                    "evidence_ids": ["ev-must"],
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
                            "text": "Built workforce dashboards and reporting workflows.",
                            "evidence_ids": ["ev-core"],
                        }
                    ],
                }
            ],
            "projects": [],
            "certifications": [],
        }
    )


def test_mode_targets_are_monotonic_and_aggressive_targets_eighty_five() -> None:
    assert resume_v5._coverage_target("slight") == 55.0
    assert resume_v5._coverage_target("medium") == 70.0
    assert resume_v5._coverage_target("aggressive") == 85.0


def test_coverage_denominator_contains_only_writer_supported_requirements(monkeypatch) -> None:
    plan = {
        "requirement_refs": [
            {
                "requirement_id": "R-MUST",
                "priority": "MUST_HAVE",
                "match_status": "DIRECT",
                "evidence_ids": ["ev-must"],
            },
            {
                "requirement_id": "R-CORE",
                "priority": "CORE_RESPONSIBILITY",
                "match_status": "STRONG_TRANSFERABLE",
                "evidence_ids": ["ev-core"],
            },
            {
                "requirement_id": "R-PREFERRED",
                "priority": "PREFERRED",
                "match_status": "PARTIAL",
                "evidence_ids": ["ev-preferred"],
            },
            {
                "requirement_id": "R-UNSUPPORTED",
                "priority": "MUST_HAVE",
                "match_status": "NO_EVIDENCE",
                "evidence_ids": [],
            },
        ]
    }
    monkeypatch.setattr(
        resume_v5.planner,
        "writer_context",
        lambda _plan: {
            "supported_requirements": [
                {"requirement_id": "R-MUST"},
                {"requirement_id": "R-CORE"},
                {"requirement_id": "R-PREFERRED"},
            ]
        },
    )

    result = resume_v5._supportable_requirement_coverage(
        plan=plan,
        document=_document(),
        rewrite_mode="aggressive",
    )

    # MUST (4) + CORE (3) represented out of MUST (4) + CORE (3) + PREFERRED (2).
    assert result["percent"] == 77.8
    assert result["target_percent"] == 85.0
    assert result["target_met"] is False
    assert result["supportable_requirement_count"] == 3
    assert result["represented_requirement_count"] == 2
    assert result["missing_supportable_requirement_ids"] == ["R-PREFERRED"]
    assert "R-UNSUPPORTED" not in result["missing_supportable_requirement_ids"]
    assert result["unsupported_requirements_in_denominator"] == 0


def test_coverage_prompt_preserves_truth_boundary() -> None:
    direction = resume_v5._coverage_direction(mode="aggressive", coverage_target=85.0)
    assert "85%" in direction
    assert "do_not_claim" in direction
    assert "never invent evidence" in direction
