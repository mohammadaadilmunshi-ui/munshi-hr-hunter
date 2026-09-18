from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app import application_answer_router_v1 as answer_router
from app import application_plan_question_bridge_v1 as question_bridge
from app import application_question_catalog_v1 as catalog
from app import candidate_profile_authority_v1 as profile_authority
from app import native_resume_studio as resume_contract
from app import resume_one_page_guard_v1 as one_page


def _resume_document(*, bullet_words: int = 18, summary_words: int = 42) -> resume_contract.ResumeDocument:
    bullet = " ".join(["delivered"] + [f"impact{i}" for i in range(bullet_words - 1)])
    summary = " ".join(["professional"] + [f"summary{i}" for i in range(summary_words - 1)])
    return resume_contract.ResumeDocument.model_validate(
        {
            "candidate_name": "Aadil Munshi",
            "contact": {
                "location": "New Jersey",
                "email": "candidate@example.com",
                "phone": "555-0100",
                "linkedin": "linkedin.com/in/example",
                "github": "",
                "portfolio": "",
            },
            "summary": {"text": summary, "evidence_ids": ["ev-summary"]},
            "education": [],
            "skills": [
                {
                    "label": "Analytics",
                    "skills": ["Excel", "Power BI", "Python"],
                    "evidence_ids": ["ev-skills"],
                }
            ],
            "experience": [
                {
                    "organization": "Example Company",
                    "title": "People Analytics Intern",
                    "dates": "2025",
                    "location": "New Jersey",
                    "bullets": [{"text": bullet, "evidence_ids": ["ev-exp"]}],
                }
            ],
            "projects": [],
            "certifications": [],
        }
    )


def test_catalog_contains_exactly_100_common_phrasings() -> None:
    metrics = catalog.catalog_metrics()
    assert metrics["concept_count"] == 50
    assert metrics["common_question_count"] == 100
    assert len({concept.key for concept in catalog.CONCEPTS}) == 50


def test_semantic_similarity_maps_equivalent_work_authorization_question() -> None:
    canonical = catalog.match_question(
        "Are you legally authorized to work in the United States?"
    )
    variant = catalog.match_question(
        "Are you currently authorized to work in the US?"
    )
    assert canonical["matched"] is True
    assert variant["matched"] is True
    assert canonical["question_key"] == variant["question_key"]
    assert canonical["question_key"] == "eligibility.work_authorized_us"
    assert canonical["sensitivity_class"] == "PROTECTED"


def test_dynamic_skill_year_question_becomes_one_reusable_semantic_key() -> None:
    result = catalog.match_question(
        "How many years of experience do you have with Power BI?"
    )
    assert result["matched"] is True
    assert result["canonical_concept"] == "experience.skill_years"
    assert result["question_key"] == "experience.skill_years.power.bi"
    assert result["conditions"] == {"skill": "Power BI"}
    assert result["match_type"] == "dynamic_skill_years"


def test_unknown_question_fails_closed_instead_of_guessing() -> None:
    result = catalog.match_question(
        "Describe a completely novel company-specific puzzle involving purple satellites."
    )
    assert result["matched"] is False
    requirement = catalog.requirement_from_question(
        "Describe a completely novel company-specific puzzle involving purple satellites."
    )
    assert requirement["question_family"] == "unknown"
    assert requirement["profile_fact_key"] is None
    assert requirement["question_key"].startswith("unknown.")


def test_protected_question_cannot_be_saved_in_normal_answer_vault() -> None:
    with pytest.raises(ValueError, match="Protected"):
        answer_router.save_user_answer(
            "Will you require sponsorship now or in the future?",
            "Yes",
        )


def test_apply_and_autoapply_question_paths_are_semantically_identical() -> None:
    munshi_apply = question_bridge.normalize_visible_question(
        {
            "label": "What is your desired annual compensation?",
            "required": True,
            "source": "munshi_apply",
        }
    )
    autoapply = question_bridge.normalize_visible_question(
        {
            "prompt": "What is your desired annual compensation?",
            "required": True,
            "source": "autoapply",
        }
    )
    for key in (
        "question_key",
        "question_family",
        "normalized_question",
        "profile_fact_key",
        "conditions",
        "sensitivity_class",
    ):
        assert munshi_apply[key] == autoapply[key]
    assert munshi_apply["question_key"] == "compensation.salary"


def test_plan_bridge_delegates_normalized_requirements_to_plan_v2(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_prepare(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"plan_id": "plan-test", "state": "READY_TO_APPLY"}

    monkeypatch.setattr(question_bridge.application_plan_v2, "prepare_application_plan", fake_prepare)
    result = question_bridge.prepare_application_plan_from_questions(
        preflight_package_id="package-test",
        idempotency_key="idem-test",
        visible_questions=[
            "What is your email address?",
            "Please provide your email.",
            {
                "label": "How many years of experience do you have with Power BI?",
                "field_id": "screening-7",
            },
        ],
    )

    # Equivalent email wording collapses to one concept when no provider field ID differs.
    requirements = captured["answer_requirements"]
    assert len(requirements) == 2
    assert requirements[0]["question_key"] == "contact.email"
    assert requirements[1]["question_key"] == "experience.skill_years.power.bi"
    assert requirements[1]["conditions"] == {"skill": "Power BI"}
    assert result["question_bridge"]["normalized_question_count"] == 2


def test_profile_authority_recognizes_actual_active_master_resume(monkeypatch) -> None:
    source = {
        "source_id": "src-1",
        "label": "Master Resume.pdf",
        "source_kind": "PDF",
        "content_sha256": "abc123",
        "updated_at": "2026-09-12 12:00:00",
    }
    extraction = {
        "extraction_id": "extract-1",
        "source_sha256": "abc123",
        "status": "CONFIRMED",
        "profile": {"contact": {"full_name": "Aadil Munshi"}},
    }
    truth = {
        "profile_revision": 4,
        "profile_digest": "truth-digest",
        "facts": [
            {"key": "contact.full_name", "value": "Aadil Munshi"},
            {"key": "contact.email", "value": "candidate@example.com"},
        ],
    }

    monkeypatch.setattr(profile_authority.resume_v3.v2.v1, "active_source", lambda: source)
    monkeypatch.setattr(
        profile_authority.resume_v3,
        "latest_profile_for_source",
        lambda source_id: extraction if source_id == "src-1" else None,
    )
    monkeypatch.setattr(
        profile_authority.profile_overrides,
        "resolve_profile",
        lambda record: record["profile"],
    )
    monkeypatch.setattr(
        profile_authority.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: truth,
    )

    snapshot = profile_authority.authority_snapshot()
    assert snapshot["state"] == "READY"
    assert snapshot["master_resume"]["configured"] is True
    assert snapshot["master_resume"]["label"] == "Master Resume.pdf"
    assert snapshot["structured_fact_count"] == 2
    assert profile_authority.master_resume()["source"] == "native_resume_sources"


def test_one_page_guard_rejects_bullet_that_old_budget_allowed() -> None:
    document = _resume_document(bullet_words=35, summary_words=42)
    # Existing contract permits <=42-word bullets, which is exactly the kind of
    # content that can wrap one last bullet onto a second DOCX page.
    assert resume_contract.ats_readiness_issues(document) == []
    density = one_page.density_metrics(document)
    assert density["safe"] is False
    assert any("bullet(s)_over_30_safe_words" in issue for issue in density["issues"])


def test_one_page_guard_accepts_compact_readable_document() -> None:
    document = _resume_document(bullet_words=22, summary_words=48)
    density = one_page.density_metrics(document)
    assert density["safe"] is True
    assert density["word_count"] <= one_page.MAX_SAFE_WORDS
    assert density["estimated_lines"] <= one_page.MAX_SAFE_ESTIMATED_LINES


def test_product_routes_use_unified_profile_and_one_page_resume() -> None:
    root = Path(__file__).resolve().parents[1]
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    resume_entry = (root / "app" / "resume_studio_page.py").read_text(encoding="utf-8")
    assert "profile_workspace_v4" in shell
    assert "profile_authority.master_resume" in shell
    assert "profile_authority.candidate_facts" in shell
    assert "resume_studio_page_v5" in resume_entry


def test_candidate_truth_projections_use_profile_evidence_path() -> None:
    root = Path(__file__).resolve().parents[1]
    autofill = (root / "app" / "application_answer_autofill_v1.py").read_text(
        encoding="utf-8"
    )
    answer_v2 = (root / "app" / "answer_brain_v2.py").read_text(encoding="utf-8")
    assert 'source="candidate_truth_projection"' not in autofill
    assert "save_profile_projection_answer" in autofill
    assert "def save_profile_projection_answer" in answer_v2
