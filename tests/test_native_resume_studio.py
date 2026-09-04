from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.native_resume_studio import (
    CertificationItem,
    ContactInfo,
    EducationItem,
    EvidenceText,
    ExperienceItem,
    ProjectItem,
    ResumeDocument,
    SkillGroup,
    ats_readiness_issues,
    document_word_count,
    evidence_ids,
    render_ats_html,
)


def _document() -> ResumeDocument:
    return ResumeDocument(
        candidate_name="Test Candidate",
        contact=ContactInfo(
            location="New Jersey, United States",
            email="candidate@example.test",
            phone="555-0100",
            linkedin="linkedin.com/in/test-candidate",
            github="github.com/test-candidate",
            portfolio="example.test",
        ),
        summary=EvidenceText(
            text="Human resource analytics candidate with recruiting operations and people analytics experience.",
            evidence_ids=["fact-summary"],
        ),
        education=[
            EducationItem(
                institution="Example University",
                degree="Master of Science, Human Resource Analytics",
                dates="Dec 2026",
                gpa="GPA: 4.0/4.0",
                evidence_ids=["fact-education"],
            )
        ],
        skills=[
            SkillGroup(
                label="Data & Analytics",
                skills=["Excel", "Power BI", "Python", "Excel"],
                evidence_ids=["fact-skills"],
            )
        ],
        experience=[
            ExperienceItem(
                organization="Example Company",
                title="HR Operations Intern",
                dates="Jul 2024 - Jan 2025",
                bullets=[
                    EvidenceText(
                        text="Coordinated recruiting operations and maintained candidate records for active requisitions.",
                        evidence_ids=["fact-experience-1"],
                    ),
                    EvidenceText(
                        text="Built Excel reporting workflows to support hiring and onboarding follow-up.",
                        evidence_ids=["fact-experience-2"],
                    ),
                ],
            )
        ],
        projects=[
            ProjectItem(
                name="People Analytics Project",
                bullets=[
                    EvidenceText(
                        text="Built a Power BI dashboard from verified HR reporting data.",
                        evidence_ids=["fact-project"],
                    )
                ],
            )
        ],
        certifications=[
            CertificationItem(
                name="Excel Skills for Business Job Simulation",
                issuer="Example Provider",
                evidence_ids=["fact-cert"],
            )
        ],
    )


def test_document_requires_evidence_for_resume_claims() -> None:
    with pytest.raises(ValidationError):
        EvidenceText(text="Unsupported claim", evidence_ids=[])


def test_em_dash_is_rejected_in_candidate_facing_content() -> None:
    with pytest.raises(ValidationError, match="em dashes"):
        EvidenceText(text="HR analytics — recruiting operations", evidence_ids=["fact-1"])


def test_skills_are_deduplicated_without_reordering() -> None:
    document = _document()
    assert document.skills[0].skills == ["Excel", "Power BI", "Python"]


def test_evidence_is_retained_for_audit_but_never_rendered() -> None:
    document = _document()
    ids = evidence_ids(document)
    assert "fact-experience-1" in ids
    assert "fact-cert" in ids
    html = render_ats_html(document)
    for evidence_id in ids:
        assert evidence_id not in html


def test_renderer_is_single_column_text_first_and_escapes_content() -> None:
    document = _document().model_copy(update={
        "summary": EvidenceText(
            text="Analytics <candidate> with HR reporting experience.",
            evidence_ids=["fact-summary"],
        )
    })
    html = render_ats_html(document)
    assert "PROFESSIONAL SUMMARY" in html
    assert "WORK EXPERIENCE" in html
    assert "CERTIFICATIONS &amp; ACHIEVEMENTS" in html
    assert "Analytics &lt;candidate&gt;" in html
    assert "grid-template" not in html
    assert "column-count" not in html
    assert "display: flex" in html  # entry headers only; the page itself remains single column
    assert "position: absolute" not in html


def test_readiness_budget_reports_long_summary_bullets_and_document() -> None:
    document = _document()
    long_summary = EvidenceText(text=" ".join(["analytics"] * 91), evidence_ids=["fact-summary"])
    long_bullet = EvidenceText(text=" ".join(["operations"] * 43), evidence_ids=["fact-experience"])
    long_experience = document.experience[0].model_copy(update={"bullets": [long_bullet]})
    oversized = document.model_copy(update={
        "summary": long_summary,
        "experience": [long_experience],
        "projects": [
            ProjectItem(
                name="Large Evidence Project",
                bullets=[EvidenceText(text=" ".join(["analysis"] * 42), evidence_ids=["fact-project"]) for _ in range(6)],
            )
            for _ in range(12)
        ],
    })
    issues = ats_readiness_issues(oversized)
    assert "summary_over_90_words" in issues
    assert "experience_0_bullet_0_over_42_words" in issues
    assert "document_over_900_words" in issues
    assert document_word_count(oversized) > 900


def test_normal_document_has_no_v1_budget_issue() -> None:
    document = _document()
    assert ats_readiness_issues(document) == []
    assert document_word_count(document) < 900
