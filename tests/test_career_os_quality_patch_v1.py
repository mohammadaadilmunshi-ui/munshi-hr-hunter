from __future__ import annotations

import sqlite3
from pathlib import Path

from app import career_os_quality_patch_v1 as patch
from app.deterministic_profile_extractor_v1 import parse_profile


_SAMPLE = """Mohammad Aadil Vasim Munshi
Palmyra, NJ | person@example.com | (856) 328-4514 | linkedin.com/in/example

PROFESSIONAL SUMMARY
Master's candidate in Human Resource Analytics with HR operations experience.

EDUCATION
Montclair State University | Montclair, NJ
M.S. Human Resource and Analytics | 2025 - 2026

EXPERIENCE
Toyota Connected India | Bengaluru, India
HR Recruitment & Operations Intern | Jul 2024 - Jan 2025
• Supported 15+ hires/month.
• Reduced onboarding delays by 30%.

SAS Retail Services | New Jersey
Retail Data Collector | Aug 2026 - Present
• Audited shelves and collected retail data.

PROJECTS
HR Analytics Dashboard
• Built a Power BI reporting workflow.

SKILLS
Analytics: Excel, Power BI, Tableau, Python
ATS: Workday | Greenhouse | BambooHR

CERTIFICATIONS
Google Data Analytics Certificate

LANGUAGES
English, Hindi

WORK AUTHORIZATION
F-1 student. OPT eligible in the United States. Will require sponsorship long-term. Open to hybrid and on-site roles.
"""


def test_deterministic_profile_parser_needs_no_model_and_preserves_resume_facts() -> None:
    profile = parse_profile(_SAMPLE)
    assert profile["contact"]["full_name"] == "Mohammad Aadil Vasim Munshi"
    assert profile["contact"]["email"] == "person@example.com"
    assert profile["education"][0]["institution"] == "Montclair State University"
    assert profile["experience"][0]["employer"] == "Toyota Connected India"
    assert profile["experience"][0]["title"] == "HR Recruitment & Operations Intern"
    assert profile["experience"][0]["bullets"][0] == "Supported 15+ hires/month."
    assert profile["experience"][1]["title"] == "Retail Data Collector"
    assert profile["skills"][0] == {
        "category": "Analytics",
        "skills": ["Excel", "Power BI", "Tableau", "Python"],
    }
    assert profile["application_defaults"]["visa_or_permit"] == "F-1 / OPT"
    assert profile["application_defaults"]["sponsorship_required"] is True
    assert profile["application_defaults"]["work_authorization_country"] == "United States"


def test_deterministic_parser_does_not_infer_voluntary_self_id() -> None:
    profile = parse_profile(_SAMPLE + "\nMuslim Student Association volunteer\n")
    serialized = repr(profile).casefold()
    assert "gender" not in serialized
    assert "ethnicity" not in serialized
    assert "disability" not in serialized
    assert "veteran" not in serialized
    assert "religion" not in serialized


def test_job_extraction_counter_uses_first_seen_window(tmp_path: Path, monkeypatch) -> None:
    db = tmp_path / "counter.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("CREATE TABLE jobs(first_seen_at TEXT)")
        connection.execute("INSERT INTO jobs VALUES(datetime('now','-30 minutes'))")
        connection.execute("INSERT INTO jobs VALUES(datetime('now','-12 hours'))")
        connection.execute("INSERT INTO jobs VALUES(datetime('now','-48 hours'))")
        connection.commit()
    finally:
        connection.close()

    monkeypatch.setattr(patch, "get_connection", lambda: sqlite3.connect(db))
    assert patch.jobs_extracted_count(1) == 1
    assert patch.jobs_extracted_count(24) == 2
    assert patch.jobs_extracted_count(0) == 3


def test_quality_patch_source_contracts_are_additive() -> None:
    root = Path(__file__).resolve().parent.parent
    patch_source = (root / "app" / "career_os_quality_patch_v1.py").read_text(encoding="utf-8")
    profile_source = (root / "app" / "deterministic_profile_extractor_v1.py").read_text(encoding="utf-8")
    shell_source = (root / "app" / "product_shell.py").read_text(encoding="utf-8")

    assert "IntersectionObserver" in patch_source
    assert "Load more jobs" in patch_source
    assert "jobs_previous" not in patch_source
    assert "jobs_next" not in patch_source
    assert "page_size=_BATCH_SIZE" in patch_source
    assert '"Past 24 hours": 24' in patch_source
    assert "product_v22._stat_card" in patch_source

    assert "httpx" not in profile_source
    assert "api.openai.com" not in profile_source
    assert 'model="deterministic-local-v1"' in profile_source

    v22_index = shell_source.index("install_product_v22(product_pages)")
    chooser_index = shell_source.index("install_resume_engine_selector(product_pages)")
    patch_index = shell_source.index("install_career_os_quality_patch(product_pages)")
    assert v22_index < chooser_index < patch_index
    assert "profile_workspace_v1.render = profile_workspace_v3.render" in shell_source
