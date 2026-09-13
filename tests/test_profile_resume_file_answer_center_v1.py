from __future__ import annotations

from pathlib import Path


def _source(path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / path).read_text(encoding="utf-8")


def test_profile_surfaces_master_resume_as_first_class_file_object() -> None:
    workspace = _source("app/profile_workspace_v4.py")
    resume_ui = _source("app/master_resume_file_ui_v1.py")

    assert "master_resume_ui.render_compact(authority)" in workspace
    assert "MASTER RESUME AUTHORITY" in resume_ui
    assert "Edit in Resume Studio" in resume_ui
    assert 'st.button("Preview resume"' in resume_ui
    assert 'st.button("Edit source"' in resume_ui
    assert 'st.button("Replace file"' in resume_ui
    assert "Download source text" in resume_ui
    assert "Save as new Master Resume revision" in resume_ui
    assert "Candidate Truth and derived answers must be rebuilt/reconfirmed" in resume_ui
    assert "Advanced source details" in resume_ui
    assert "📄" not in resume_ui


def test_master_resume_ui_does_not_pretend_original_binary_is_available() -> None:
    resume_ui = _source("app/master_resume_file_ui_v1.py")

    assert "not the original PDF/DOCX bytes" in resume_ui
    assert "normalized source text" in resume_ui
    assert "extract_uploaded_source" in resume_ui
    assert "save_confirmed_source" in resume_ui


def test_answer_center_supports_batch_protected_and_future_reuse() -> None:
    answer_ui = _source("app/profile_answer_center_ui_v1.py")
    workspace = _source("app/profile_workspace_v4.py")

    assert "answer_center.render(authority)" in workspace
    assert "Complete reusable profile" in answer_ui
    assert "Save completed reusable answers" in answer_ui
    assert "Protected & self-ID" in answer_ui
    assert "profile_v3._edit_application_defaults()" in answer_ui
    assert "Teach MUNSHI for future use" in answer_ui
    assert "Save for future applications" in answer_ui
    assert "answer_router.save_user_answer" in answer_ui
    assert "What does the remaining coverage mean?" in answer_ui


def test_answer_center_keeps_provider_catalog_internal_and_contextual_questions_optional() -> None:
    answer_ui = _source("app/profile_answer_center_ui_v1.py")
    workspace = _source("app/profile_workspace_v4.py")
    answer_v3 = _source("app/profile_answer_center_ui_v3.py")

    assert "Provider wording stays internal" in answer_ui
    assert "not currently counted as known" in answer_ui
    assert "secure or application-time path" in workspace
    assert "Handled when an application needs it" in answer_ui
    assert "actual meanings MUNSHI does not currently know" in answer_v3
    assert "Recognition phrasings" not in answer_ui
    assert "Common phrasings" not in answer_ui
    assert "Advanced: view the 100-phrase recognition catalog" not in answer_ui
