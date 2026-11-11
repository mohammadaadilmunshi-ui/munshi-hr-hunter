from pathlib import Path

from app import native_resume_presentation_v1 as presentation
from app.native_resume_studio import ResumeDocument


ROOT = Path(__file__).resolve().parents[1]


def _document() -> ResumeDocument:
    return ResumeDocument.model_validate(
        {
            "candidate_name": "Test Candidate",
            "contact": {"email": "candidate@example.com", "location": "New Jersey"},
            "summary": {"text": "Evidence-backed HR analytics candidate.", "evidence_ids": ["e1"]},
            "education": [
                {
                    "institution": "Example University",
                    "degree": "MS Human Resource Analytics",
                    "dates": "2026",
                    "evidence_ids": ["e2"],
                }
            ],
            "skills": [
                {"label": "Analytics", "skills": ["Excel", "Power BI"], "evidence_ids": ["e3"]}
            ],
            "experience": [
                {
                    "organization": "Example Co",
                    "title": "HR Intern",
                    "dates": "2025",
                    "bullets": [
                        {"text": "Built a recruiting operations dashboard.", "evidence_ids": ["e4"]}
                    ],
                }
            ],
        }
    )


def test_structured_native_resume_can_render_standard_and_jake_canvas() -> None:
    document = _document()
    standard = presentation.render_document_html(document, {"template": "Standard", "font": "Arial"})
    jake = presentation.render_document_html(document, {"template": "Jake", "font": "Computer Modern"})
    assert 'id="munshi-presentation-v1"' in standard
    assert "Arial, Helvetica" in standard
    assert "Latin Modern Roman" in jake
    assert "Test Candidate" in standard
    assert "WORK EXPERIENCE" in standard


def test_master_resume_source_preview_is_paper_not_textarea() -> None:
    rendered = presentation.render_master_source_html(
        "TEST CANDIDATE\n\nPROFESSIONAL SUMMARY\nEvidence-backed candidate.\n\nWORK EXPERIENCE\n• Built dashboards",
        {"template": "Standard", "font": "Arial", "font_size": 10.5},
    )
    assert 'class="paper"' in rendered
    assert 'class="source-heading"' in rendered
    assert 'class="source-bullet"' in rendered
    assert "textarea" not in rendered.casefold()


def test_profile_uses_visual_master_resume_surface() -> None:
    source = (ROOT / "app" / "profile_workspace_v4.py").read_text(encoding="utf-8")
    assert "master_resume_visual_ui_v2" in source
    assert "master_resume_file_ui_v1 as master_resume_ui" not in source


def test_resume_studio_entrypoint_routes_to_v6() -> None:
    source = (ROOT / "app" / "resume_studio_page.py").read_text(encoding="utf-8")
    assert "resume_studio_page_v6" in source
    assert "render = render_v6" in source


def test_v6_preserves_checked_artifact_authority() -> None:
    source = (ROOT / "app" / "resume_studio_page_v6.py").read_text(encoding="utf-8")
    assert "presentation-only" in source
    assert "immutable Standard V5 artifact" in source
    assert "v5.render()" in source
