"""Grounded JD v1.1: remove structural noise and JSON duplicate keywords."""
from app import jd_intelligence_v1 as jd
from tests.test_stage_b_jd_intelligence import _job


def test_realistic_bob_evans_shape_sections_duplicates_and_provenance(hunter_db):
    job_id = _job(
        description=(
            "Job Summary:\nAn HR coordinator supports employees.\n"
            "Duties and Responsibilities:\n"
            "Coordination of new hire onboarding and employee records.\n"
            "Completes payroll documentation.\n"
            "Education and Experience:\nEducation Level:\n"
            "Bachelor's degree required.\nExperience:\n"
            "Skills:\nExcellent organizational and documentation skills.\n"
            "Must maintain confidentiality.\n"
        ),
        responsibilities="",
        qualifications="",
        preferred_qualifications="",
        preferred_skills="",
        skills_keywords=(
            '["Excellent organizational and documentation skills.", '
            '"Must maintain confidentiality.", '
            '"Microsoft Excel", "People Analytics"]'
        ),
        work_authorization="Not specified",
    )
    result = jd.analyze_job(job_id, persist=False)
    phrases = [r["exact_text"] for r in result["requirements"]]
    assert "Job Summary:" not in phrases
    assert "Education Level:" not in phrases
    assert "Skills:" not in phrases
    assert "Not specified" not in phrases
    assert phrases.count("Excellent organizational and documentation skills.") == 1
    assert phrases.count("Must maintain confidentiality.") == 1
    assert "Microsoft Excel" in phrases
    assert "People Analytics" in phrases
    responsibilities = [
        r for r in result["requirements"]
        if r["source_field"] == "description_raw"
        and "onboarding" in r["exact_text"]
    ]
    assert len(responsibilities) == 1
    assert responsibilities[0]["priority"] == "CORE_RESPONSIBILITY"
    assert responsibilities[0]["type"] == "RESPONSIBILITY"
    assert result["diagnostics"]["llm_calls"] == 0
    assert result["submission_authority"] is False
    assert all(not key.startswith("[") for key in result["keywords"])
    assert all(len(key.split()) <= 8 for key in result["keywords"])
    raw = jd.safe_owned_job_snapshot(job_id)["job"]
    for requirement in result["requirements"]:
        source = str(raw[requirement["source_field"]] or "")
        assert source[requirement["source_start"]:requirement["source_end"]] == requirement["exact_text"]


def test_explicit_sponsorship_kept_without_inferring_work_authorization(hunter_db):
    job_id = _job(
        description="We cannot sponsor employment visas. Must be authorized to work.",
        skills_keywords='["Excel", "Onboarding"]',
        work_authorization="Not specified",
    )
    result = jd.analyze_job(job_id, persist=False)
    assert any(r["type"] == "SPONSORSHIP" for r in result["requirements"])
    assert all(r["exact_text"].casefold() != "not specified" for r in result["requirements"])


def test_metadata_not_mistaken_as_skill_and_no_single_letter_tool_false_positive():
    assert jd._looks_like_tool("Coordinating volunteers and tracking records") is False
    assert jd._looks_like_tool("Knowledge of SAP SuccessFactors") is True
    assert jd._looks_like_tool("Excel reporting") is True
