from __future__ import annotations

import base64
import json
import os
import sys
import types

import pytest

from app import database
from app import native_resume_service_v3 as v3
from app.profile_extraction_bridge_v1 import (
    LOCAL_PROFILE_MODEL,
    extract_profile_from_source as local_profile_extract,
)
from app.profile_local_extractor_v1 import extract_profile_data


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def test_pdf_resume_text_is_extracted_locally(monkeypatch) -> None:
    class FakePage:
        def extract_text(self):
            return "Aadil Munshi\nPeople Analytics\nPower BI"

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

        def __init__(self, _stream, strict=False):
            assert strict is False

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))
    text, kind = v3.extract_uploaded_source("master-resume.PDF", b"synthetic-pdf")
    assert "People Analytics" in text
    assert kind == "text_upload"


def test_pdf_without_extractable_text_fails_closed(monkeypatch) -> None:
    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

        def __init__(self, _stream, strict=False):
            pass

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))
    with pytest.raises(ValueError, match="no extractable text"):
        v3.extract_uploaded_source("scan.pdf", b"synthetic-scan")


def test_profile_schema_does_not_create_voluntary_self_id_fields() -> None:
    schema_text = json.dumps(v3.CandidateProfileExtract.model_json_schema()).casefold()
    for forbidden in ("gender", "ethnicity", "race", "religion", "disability", "veteran", "marital"):
        assert forbidden not in schema_text


def test_profile_snapshot_plaintext_fallback_is_explicit(hunter_db, monkeypatch) -> None:
    monkeypatch.setattr(v3, "vault_available", lambda: False)
    source = v3.save_confirmed_source(
        content_text="Aadil Munshi\nPeople Analytics Analyst\nPower BI",
        label="Master Resume",
        source_kind="pasted_text",
    )
    profile = v3.CandidateProfileExtract(
        professional_summary="People analytics candidate with Power BI experience.",
        skills=[v3.SkillCategory(category="Data & Analytics", skills=["Power BI"])],
    )
    record = v3._persist_profile(source=source, profile=profile, model="test-model", response_id="resp-test")
    assert record["storage_mode"] == "sqlite_plaintext"
    assert record["status"] == "DRAFT"
    assert record["profile"]["skills"][0]["skills"] == ["Power BI"]


def test_profile_snapshot_uses_aes_gcm_vault_when_available(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = v3.save_confirmed_source(
        content_text="Aadil Munshi\nHR Analytics\nExcel",
        label="Master Resume",
        source_kind="pasted_text",
    )
    profile = v3.CandidateProfileExtract(
        professional_summary="HR analytics candidate.",
        skills=[v3.SkillCategory(category="Data & Analytics", skills=["Excel"])],
    )
    record = v3._persist_profile(source=source, profile=profile, model="test-model", response_id="resp-secure")
    assert record["storage_mode"] == "aes_gcm_vault"
    assert record["profile"]["professional_summary"] == "HR analytics candidate."

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT profile_json,vault_label,storage_mode FROM native_resume_profile_extracts WHERE extraction_id=?",
            (record["extraction_id"],),
        ).fetchone()
        assert row is not None
        assert row["profile_json"] is None
        assert row["vault_label"]
        assert row["storage_mode"] == "aes_gcm_vault"
    finally:
        connection.close()


def test_confirmed_profile_is_owner_scoped(hunter_db, monkeypatch) -> None:
    monkeypatch.setattr(v3, "vault_available", lambda: False)
    source = v3.save_confirmed_source(
        content_text="Aadil Munshi\nHuman Resources",
        label="Master Resume",
        source_kind="pasted_text",
    )
    record = v3._persist_profile(
        source=source,
        profile=v3.CandidateProfileExtract(professional_summary="Human resources candidate."),
        model="test-model",
        response_id="resp-confirm",
    )
    confirmed = v3.confirm_profile_extract(record["extraction_id"])
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["confirmed_at"]


def _representative_resume() -> str:
    return """Jordan Candidate
Newark, NJ | (973) 555-0199 | jordan@example.com | linkedin.com/in/jordan-candidate | https://portfolio.example
SUMMARY
People analytics master's candidate with HR operations and reporting experience.
EDUCATION
Example State University, Newark, NJ
Master of Science in Human Resource Analytics | Expected Dec 2026 | GPA: 4.0/4.0
Coursework: People Analytics, Workforce Analytics, Data Mining
Example University, Boston, MA
Bachelor of Science in Business Administration | GPA: 3.8/4.0
TECHNICAL SKILLS
● Data & Analytics: Advanced Excel | Power BI | Tableau | Python | Pandas
● HR Operations: Recruiting Coordination | Onboarding Support | Employee Records
● Compliance & Tools: Data Privacy | HR Documentation
Certifications:
Excel Skills Job Simulation - Example Institute | HR Analytics Certificate - Example Academy
PROJECTS
People Analytics Dashboard Power BI, Tableau, Excel
● Built 3 dashboards from 500+ HR records and reduced reporting preparation by 35%.
Predictive Analytics Study Python, Pandas, Scikit-learn
● Compared classification models and documented precision, recall, and F1 score.
WORK EXPERIENCE
Example Mobility July 2024 - Jan 2025
Human Resources Operations Intern
● Coordinated onboarding for 15+ hires monthly and maintained 100% documentation completion.
● Reduced scheduling delays by 30% through structured follow-up.
Example Services Nov 2023 - July 2024
HR and Accounting Intern
● Reconciled payroll and benefits records in Excel.
"""


def test_local_profile_extractor_builds_rich_structure_without_ai() -> None:
    profile = extract_profile_data(_representative_resume())
    assert profile["contact"]["full_name"] == "Jordan Candidate"
    assert profile["contact"]["location"] == "Newark, NJ"
    assert profile["contact"]["email"] == "jordan@example.com"
    assert len(profile["education"]) == 2
    assert profile["education"][0]["degree"] == "Master of Science"
    assert profile["education"][0]["field"] == "Human Resource Analytics"
    assert len(profile["experience"]) == 2
    assert profile["experience"][0]["employer"] == "Example Mobility"
    assert profile["experience"][0]["title"] == "Human Resources Operations Intern"
    assert "15+ hires" in profile["experience"][0]["bullets"][0]
    assert len(profile["projects"]) == 2
    assert profile["projects"][0]["tools"] == ["Power BI", "Tableau", "Excel"]
    assert len(profile["skills"]) == 3
    assert "Power BI" in profile["skills"][0]["skills"]
    assert len(profile["certifications"]) == 2


def test_local_profile_extractor_never_invents_application_defaults_or_metrics() -> None:
    profile = extract_profile_data(_representative_resume())
    defaults = profile["application_defaults"]
    assert defaults["work_authorization_country"] == ""
    assert defaults["visa_or_permit"] == ""
    assert defaults["sponsorship_required"] is None
    assert defaults["willing_to_relocate"] is None
    serialized = json.dumps(profile)
    assert "92%" not in serialized
    assert "35%" in serialized
    assert "15+" in serialized
    assert "100%" in serialized
    assert "30%" in serialized


def test_profile_bridge_persists_local_snapshot_without_openai(hunter_db, monkeypatch) -> None:
    monkeypatch.setattr(v3, "vault_available", lambda: False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source = v3.save_confirmed_source(
        content_text=_representative_resume(),
        label="Master Resume",
        source_kind="pasted_text",
    )
    record = local_profile_extract(source)
    assert record["model_name"] == LOCAL_PROFILE_MODEL
    assert record["status"] == "DRAFT"
    assert record["profile"]["contact"]["full_name"] == "Jordan Candidate"
    assert record["profile"]["projects"][0]["name"] == "People Analytics Dashboard"
    assert record["profile"]["application_defaults"]["sponsorship_required"] is None
