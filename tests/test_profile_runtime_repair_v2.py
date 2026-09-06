from __future__ import annotations

from pathlib import Path

from app.deterministic_profile_extractor_v2 import parse_profile, repair_resume_text


_MESSY_RESUME = """Mohammad Aadil Vasim Munshi
Palmyra, NJ | person@example.com | (856) 328-4514

PROFESSIONAL SUMMARY
Master's candidate in Human Resource Analytics with HR operations experience.

EDUCATION
Montclair State University | Montclair, NJ
Master of Science in Human Resource Analytics | Expected Dec 2026 | GPA: 4.0/4.0
Coursework: HR Analytics, People Analytics
University of Debrecen | Debrecen, Hungary
Bachelor of Science in Business Administration and Management | GPA: 4.31/5.0

EXPERIENCE
Human Resource Recruitment & Operations Intern
Toyota
July 2024 – Jan 2025
• Supported 4-6 active requisitions and 60+ candidate records monthly by tracking candidate
status, interview schedules, recruiter follow-ups, and hiring-manager next steps.
• Coordinated onboarding for 15+ hires monthly, achieving 100% documentation completion
across orientation, pre-employment checks, employee file updates, and HR compliance checklists.
• Shortened interview scheduling turnaround by 1 business day, reduced delays by 30%, and
maintained 99% employee file accuracy through ATS/HRIS-style data entries.
HR and Accounting Intern
Munshi Sales
Nov 2023 – July 2024
• Organized 100+ payroll, benefits, and employee documentation records, supporting audit-
ready files and faster retrieval of confidential HR information.
• Reconciled payroll and benefits entries in Excel using VLOOKUP, PivotTables, validation
checks, and exception tracking, reducing manual review errors by 25%.
• Built Excel trackers for payroll, benefits status, attendance, and employee documentation,
reducing lookup time by 30% and improving response speed.

PROJECTS & ANALYTICS
People Analytics & Benefits Operations Projects
• Built 3 Power BI/Tableau dashboards from 500+ HR, benefits, payroll, and recruiting records;
reduced weekly reporting prep from about 3 hours to under 2 hours.
AI-Native Data Mining & Predictive Analytics Study
• Cleaned 2,800+ records and 20+ variables with Python/Pandas by handling missing values,
duplicates, outliers, categorical encoding, feature scaling, and train/test splits.

SKILLS
Data & Analytics: Advanced Excel, PivotTables, VLOOKUP, XLOOKUP, Power Query, Power BI, Tableau, Python, Pandas
HR Operations: Recruiting Coordination, Candidate Tracking, Requisition Support, Interview Scheduling
"""


def test_v2_repairs_wrapped_lines_without_creating_fake_jobs() -> None:
    profile = parse_profile(_MESSY_RESUME)
    assert len(profile["education"]) == 2
    assert len(profile["experience"]) == 2
    assert len(profile["projects"]) == 2

    first = profile["experience"][0]
    assert first["title"] == "Human Resource Recruitment & Operations Intern"
    assert first["employer"] == "Toyota"
    assert len(first["bullets"]) == 3
    assert "recruiter follow-ups" in first["bullets"][0]
    assert "pre-employment checks" in first["bullets"][1]
    assert "employee file accuracy" in first["bullets"][2]

    second = profile["experience"][1]
    assert second["title"] == "HR and Accounting Intern"
    assert second["employer"] == "Munshi Sales"
    assert len(second["bullets"]) == 3
    assert all(item.get("title") != "Role" for item in profile["experience"])


def test_v2_recognizes_section_heading_variants() -> None:
    repaired = repair_resume_text(_MESSY_RESUME)
    assert "\nPROJECTS\n" in repaired
    profile = parse_profile(_MESSY_RESUME)
    assert profile["projects"][0]["name"] == "People Analytics & Benefits Operations Projects"


def test_profile_rebuild_wiring_is_draft_only_and_additive() -> None:
    root = Path(__file__).resolve().parent.parent
    shell = (root / "app" / "product_shell.py").read_text(encoding="utf-8")
    runtime = (root / "app" / "profile_runtime_repair_v2.py").read_text(encoding="utf-8")
    extractor = (root / "app" / "deterministic_profile_extractor_v2.py").read_text(encoding="utf-8")

    quality_index = shell.index("install_career_os_quality_patch(product_pages)")
    repair_index = shell.index("install_profile_runtime_repair_v2()")
    assert quality_index < repair_index
    assert "Rebuild preview from current Master Resume" in runtime
    assert "confirm_profile_extract" not in runtime
    assert 'model="deterministic-local-v2"' in extractor
    assert "api.openai.com" not in extractor
    assert "httpx" not in extractor
