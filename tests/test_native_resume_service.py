from __future__ import annotations

import importlib
import io
import json
import zipfile

import pytest

from app import database
from app.native_resume_service import (
    active_source,
    analyze_document,
    build_evidence_bundle,
    ensure_schema,
    extract_uploaded_source,
    generate_resume,
    get_version,
    list_versions,
    model_status,
    native_resume_authority_enabled,
    render_docx_bytes,
    save_confirmed_source,
    validate_document_evidence,
    version_diff,
)
from app.native_resume_studio import ResumeDocument, render_ats_html


SOURCE_TEXT = """Mohammad Aadil Vasim Munshi
Palmyra, New Jersey, United States | aadil@example.com | (856) 555-1212 | linkedin.com/in/aadil | github.com/aadil | munshi.systems
Montclair State University | Master of Science, Human Resource Analytics | Dec 2026 | GPA: 4.0/4.0
Toyota | Human Resource Recruitment & Operations Intern | Jul 2024 - Jan 2025
Supported 4-6 active requisitions and 60+ candidate records monthly by tracking candidate status, interview schedules, recruiter follow-ups, and hiring-manager next steps.
Coordinated onboarding for 15+ hires monthly and maintained 100% documentation completion across orientation and pre-employment records.
Shortened interview scheduling turnaround by 1 business day and reduced delays by 30% through structured follow-up tracking.
Data & Analytics: Advanced Excel, PivotTables, XLOOKUP, Power Query, Power BI, Tableau, Google Sheets, Python, Pandas
People Analytics Dashboard Project
Built Power BI and Tableau dashboards from 500+ HR records and reduced weekly reporting preparation from about 3 hours to under 2 hours.
Excel Skills for Business Job Simulation | Goldman Sachs
Veteran status: private voluntary self-identification
"""


def _job() -> int:
    connection = database.get_connection()
    try:
        job_id = connection.execute(
            """INSERT INTO jobs(
                job_fingerprint,source,company_name,title,location_raw,description_raw,
                responsibilities,qualifications,preferred_skills,skills_keywords,hunter_score
            ) VALUES (
                'native-resume-job','fixture','Analytics Co','People Analytics Analyst','New York, NY',
                'Build people analytics dashboards, HR reporting, recruiting analytics, and workforce insights using Excel, Power BI, Python, and stakeholder communication.',
                'Create dashboards and analyze recruiting and workforce data.',
                'Excel, Power BI, HR reporting, analytics, communication.',
                'Python and Tableau preferred.',
                'Excel,Power BI,Python,Tableau,HR Reporting',96
            )"""
        ).lastrowid
        connection.commit()
        return int(job_id)
    finally:
        connection.close()


def _bundle_ids(bundle: dict) -> dict[str, str]:
    result = {}
    for item in bundle["items"]:
        text = str(item["text"])
        for key in (
            "Mohammad Aadil", "Palmyra", "Montclair", "Toyota |", "Supported 4-6",
            "Coordinated onboarding", "Shortened interview", "Data & Analytics",
            "People Analytics Dashboard", "Built Power BI", "Excel Skills",
        ):
            if key in text:
                result[key] = str(item["evidence_id"])
    return result


def _document(bundle: dict, *, scheduling_metric: str = "30%") -> dict:
    ids = _bundle_ids(bundle)
    return {
        "schema_version": "native-resume-v1",
        "template_version": "ats-single-column-v1",
        "candidate_name": "Mohammad Aadil Vasim Munshi",
        "contact": {
            "location": "Palmyra, New Jersey, United States",
            "email": "aadil@example.com",
            "phone": "(856) 555-1212",
            "linkedin": "linkedin.com/in/aadil",
            "github": "github.com/aadil",
            "portfolio": "munshi.systems",
        },
        "summary": {
            "text": "Human Resource Analytics master's candidate with recruiting operations, people analytics, Excel, Power BI, Tableau, and Python experience.",
            "evidence_ids": [ids["Montclair"], ids["Toyota |"], ids["Data & Analytics"]],
        },
        "education": [{
            "institution": "Montclair State University",
            "degree": "Master of Science, Human Resource Analytics",
            "dates": "Dec 2026",
            "location": "",
            "gpa": "GPA: 4.0/4.0",
            "evidence_ids": [ids["Montclair"]],
        }],
        "skills": [{
            "label": "Data & Analytics",
            "skills": ["Advanced Excel", "PivotTables", "XLOOKUP", "Power Query", "Power BI", "Tableau", "Python", "Pandas"],
            "evidence_ids": [ids["Data & Analytics"]],
        }],
        "experience": [{
            "organization": "Toyota",
            "title": "Human Resource Recruitment & Operations Intern",
            "dates": "Jul 2024 - Jan 2025",
            "location": "",
            "bullets": [
                {
                    "text": "Supported 4-6 active requisitions and 60+ candidate records monthly through candidate tracking, interview scheduling, recruiter follow-ups, and hiring-manager next steps.",
                    "evidence_ids": [ids["Supported 4-6"]],
                },
                {
                    "text": "Coordinated onboarding for 15+ hires monthly while maintaining 100% documentation completion across orientation and pre-employment records.",
                    "evidence_ids": [ids["Coordinated onboarding"]],
                },
                {
                    "text": f"Reduced interview scheduling delays by {scheduling_metric} through structured follow-up tracking and improved turnaround by 1 business day.",
                    "evidence_ids": [ids["Shortened interview"]],
                },
            ],
        }],
        "projects": [{
            "name": "People Analytics Dashboard Project",
            "subtitle": "",
            "bullets": [{
                "text": "Built Power BI and Tableau dashboards from 500+ HR records and reduced weekly reporting preparation from about 3 hours to under 2 hours.",
                "evidence_ids": [ids["Built Power BI"]],
            }],
        }],
        "certifications": [{
            "name": "Excel Skills for Business Job Simulation",
            "issuer": "Goldman Sachs",
            "evidence_ids": [ids["Excel Skills"]],
        }],
    }


def test_source_is_owner_scoped_and_sensitive_line_is_excluded(hunter_db) -> None:
    saved = save_confirmed_source(content_text=SOURCE_TEXT, label="Master evidence")
    assert saved["source_kind"] == "pasted_text"
    assert active_source()["source_id"] == saved["source_id"]
    bundle = build_evidence_bundle()
    serialized = json.dumps(bundle).casefold()
    assert "veteran status" not in serialized
    assert "private voluntary" not in serialized
    assert "toyota" in serialized


def test_same_source_is_idempotently_reactivated(hunter_db) -> None:
    first = save_confirmed_source(content_text=SOURCE_TEXT, label="One")
    second = save_confirmed_source(content_text=SOURCE_TEXT, label="Two")
    assert first["source_id"] == second["source_id"]
    assert second["label"] == "Two"


def test_docx_export_round_trips_as_importable_source(hunter_db) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    bundle = build_evidence_bundle()
    document = ResumeDocument.model_validate(_document(bundle))
    data = render_docx_bytes(document)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        assert "word/document.xml" in names
        assert "word/styles.xml" in names
        assert "[Content_Types].xml" in names
    extracted, kind = extract_uploaded_source("resume.docx", data)
    assert kind == "docx_upload"
    assert "Mohammad Aadil Vasim Munshi" in extracted
    assert "Toyota" in extracted


def test_truth_audit_rejects_unknown_evidence_and_unsupported_numbers(hunter_db) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    bundle = build_evidence_bundle()
    clean = ResumeDocument.model_validate(_document(bundle))
    assert validate_document_evidence(clean, bundle)["status"] == "PASS"

    forged = _document(bundle)
    forged["summary"]["evidence_ids"] = ["made-up-evidence"]
    with pytest.raises(ValueError, match="unknown evidence"):
        validate_document_evidence(ResumeDocument.model_validate(forged), bundle)

    unsupported = ResumeDocument.model_validate(_document(bundle, scheduling_metric="99%"))
    with pytest.raises(ValueError, match="unsupported numeric claim"):
        validate_document_evidence(unsupported, bundle)


def test_diagnostics_are_explainable_not_vendor_claims(hunter_db) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    bundle = build_evidence_bundle()
    job_id = _job()
    from app.native_resume_service import job_context
    diagnostics = analyze_document(ResumeDocument.model_validate(_document(bundle)), job_context(job_id), bundle)
    assert diagnostics["truth_audit"]["status"] == "PASS"
    assert diagnostics["ats_score_label"] == "MUNSHI ATS readiness estimate"
    assert 0 <= diagnostics["ats_readiness_estimate"] <= 100
    assert diagnostics["jd_term_coverage_percent"] > 0
    assert diagnostics["content_budget_issues"] == []


def test_generation_persists_immutable_versions_without_native_authority(hunter_db, monkeypatch) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    bundle = build_evidence_bundle()
    job_id = _job()
    payload = _document(bundle)

    import app.native_resume_service as service
    calls = []

    def fake_call(*, prompt_payload):
        calls.append(prompt_payload)
        return payload, f"resp-{len(calls)}", "gpt-test"

    monkeypatch.setattr(service, "_call_openai", fake_call)
    first = generate_resume(job_id=job_id, instruction="Prioritize analytics")
    assert first["version_number"] == 1
    assert first["parent_version_id"] is None
    assert first["status"] == "VALIDATED"
    assert first["diagnostics"]["truth_audit"]["status"] == "PASS"
    assert native_resume_authority_enabled() is False

    second = generate_resume(
        job_id=job_id,
        parent_version_id=first["version_id"],
        instruction="Make it more concise",
        locked_sections=["education", "contact"],
    )
    assert second["version_number"] == 2
    assert second["parent_version_id"] == first["version_id"]
    assert get_version(first["version_id"])["document"] == first["document"]
    assert len(list_versions(job_id=job_id)) == 2
    assert "v1" in version_diff(second["version_id"])
    assert "v2" in version_diff(second["version_id"])


def test_model_status_never_exposes_api_key(hunter_db, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-test-key")
    monkeypatch.setenv("MUNSHI_RESUME_MODEL", "gpt-5.6-terra")
    status = model_status()
    assert status["configured"] is True
    assert status["model"] == "gpt-5.6-terra"
    assert "super-secret-test-key" not in json.dumps(status)


def test_missing_api_key_fails_before_network(hunter_db, monkeypatch) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    job_id = _job()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="not configured"):
        generate_resume(job_id=job_id)
    assert list_versions(job_id=job_id) == []


def test_renderer_contains_no_evidence_ids_or_hidden_keyword_stuffing(hunter_db) -> None:
    save_confirmed_source(content_text=SOURCE_TEXT)
    bundle = build_evidence_bundle()
    document = ResumeDocument.model_validate(_document(bundle))
    html = render_ats_html(document)
    for evidence_id in {item["evidence_id"] for item in bundle["items"]}:
        assert evidence_id not in html
    assert "display:none" not in html.replace(" ", "").casefold()
    assert "PROFESSIONAL SUMMARY" in html
    assert "WORK EXPERIENCE" in html


def test_migration_027_is_additive(hunter_db) -> None:
    connection = database.get_connection()
    try:
        importlib.import_module("migrations.027_native_resume_studio").apply(connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        connection.close()
    assert "native_resume_sources" in tables
    assert "native_resume_versions" in tables
