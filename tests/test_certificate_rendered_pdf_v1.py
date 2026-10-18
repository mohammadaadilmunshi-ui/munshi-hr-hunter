"""Real Chromium render: all five certificate names remain on a single page."""
from __future__ import annotations
import io
import shutil
import sys
from pypdf import PdfReader
import pytest
from app.master_certifications_v1 import complete_certifications
from app.native_resume_studio import ResumeDocument
from app.native_resume_service import render_pdf_bytes
from tests.test_master_certifications_v1 import _wrapped_facts


def _document():
    certs=complete_certifications(_wrapped_facts())
    bullets=[
        "Coordinated onboarding for 15+ hires monthly with complete required documentation, orientation follow-up, and recruiting support.",
        "Reduced onboarding delays by 30% through candidate communication, interview scheduling, pre-employment checks, and organized records.",
        "Improved candidate satisfaction by 25% with consistent recruiting updates, clear next steps, and timely candidate coordination.",
    ]
    return ResumeDocument.model_validate({
        "candidate_name":"Example Resume Candidate",
        "contact":{"email":"candidate@example.com","location":"New Jersey","linkedin":"linkedin.com/in/example"},
        "summary":{"text":("Human Resource Analytics master's candidate with recruiting coordination, "
                           "onboarding, documentation compliance, confidential employee records, "
                           "payroll and benefits support, and Excel-based HR reporting."),
                   "evidence_ids":["e1"]},
        "education":[
            {"institution":"Montclair State University","degree":"MS Human Resource Analytics",
             "dates":"Expected Dec 2026","evidence_ids":["e1"]},
            {"institution":"University of Debrecen","degree":"BS Business Administration and Management",
             "evidence_ids":["e1"]},
        ],
        "skills":[
            {"label":"HR Operations","skills":["Recruiting Coordination","Interview Scheduling",
              "Onboarding","Employee Records","HR Documentation","Confidentiality"],
             "evidence_ids":["e1"]},
            {"label":"Data & Analytics","skills":["Excel","Power BI","Tableau","Python",
              "Pandas","HR Reporting","Data Validation"],"evidence_ids":["e1"]},
        ],
        "experience":[
            {"organization":"Toyota Connected India","title":"HR Recruitment & Operations Intern",
             "dates":"Jul 2024 - Jan 2025",
             "bullets":[{"text":b,"evidence_ids":["e1"]} for b in bullets]},
            {"organization":"Munshi Sales","title":"HR and Accounting Intern",
             "dates":"Nov 2023 - Jul 2024",
             "bullets":[
                {"text":("Organized confidential employee, payroll and benefits records "
                  "to support accurate audit-ready HR documentation."),"evidence_ids":["e1"]},
                {"text":("Reconciled payroll and benefits discrepancies using Excel "
                   "data validation and source-record follow-up."),"evidence_ids":["e1"]},
            ]},
        ],
        "projects":[
            {"name":"People Analytics & Benefits Operations Projects",
             "bullets":[{"text":("Created workforce and HR reporting dashboards "
                         "in Power BI and Tableau for recruiting and benefits."),"evidence_ids":["e1"]}]},
            {"name":"Data Mining and Predictive Analytics Study",
             "bullets":[{"text":"Cleaned and validated data using Python and Pandas.","evidence_ids":["e1"]}]},
        ],
        "certifications":certs,
    })


def test_real_pdf_contains_every_complete_certificate_and_is_one_page():
    if sys.platform == "darwin":
        pytest.skip("Remote Mac Chrome sandbox unavailable; verified with production Linux Chromium")
    if shutil.which("chromium") is None:
        pytest.skip("Actual Chromium renderer unavailable; CI checks structure separately")
    document=_document()
    data,pages=render_pdf_bytes(document)
    assert pages==1
    reader=PdfReader(io.BytesIO(data))
    text=" ".join((reader.pages[0].extract_text() or "").split())
    for cert in document.certifications:
        # The PDF may wrap between words, but must not truncate either side.
        assert cert.name in text, f"PDF lost certificate name: {cert.name!r}"
        assert cert.issuer in text, f"PDF lost certificate issuer: {cert.issuer!r}"
    assert "JP Morgan" in text
