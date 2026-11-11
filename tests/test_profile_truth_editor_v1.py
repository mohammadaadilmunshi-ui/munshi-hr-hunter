from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from app import database
from app import native_resume_service_v3 as v3
from app import profile_truth_overrides_v1 as overrides


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _extracted(hunter_db, monkeypatch) -> dict:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = v3.save_confirmed_source(
        content_text="Aadil Munshi\nSUMMARY\nHR analytics candidate.\nSKILLS\nExcel | Power BI",
        label="Master Resume",
        source_kind="pasted_text",
    )
    return v3._persist_profile(
        source=source,
        profile=v3.CandidateProfileExtract(
            professional_summary="HR analytics candidate.",
            contact=v3.ContactProfile(full_name="Aadil Munshi", location="New Jersey"),
            skills=[v3.SkillCategory(category="Data & Analytics", skills=["Excel", "Power BI"])],
        ),
        model="test-model",
        response_id="resp-profile-editor",
    )


def test_candidate_override_does_not_mutate_resume_extraction(hunter_db, monkeypatch) -> None:
    extracted = _extracted(hunter_db, monkeypatch)
    original = extracted["profile"]["professional_summary"]

    overrides.save_profile_override(extracted, "professional_summary", "Updated candidate-confirmed summary.")
    resolved = overrides.resolve_profile(extracted)

    assert original == "HR analytics candidate."
    assert extracted["profile"]["professional_summary"] == original
    assert resolved["professional_summary"] == "Updated candidate-confirmed summary."

    stored = v3.get_profile_extract(extracted["extraction_id"])
    assert stored["profile"]["professional_summary"] == original


def test_candidate_override_is_ciphertext_only(hunter_db, monkeypatch) -> None:
    extracted = _extracted(hunter_db, monkeypatch)
    secret_value = "Candidate confirmed private profile edit"
    overrides.save_profile_override(extracted, "professional_summary", secret_value)

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT ciphertext,credential_type,account_label FROM credential_secret WHERE credential_type=?",
            (overrides.PROFILE_OVERRIDE_SECRET_TYPE,),
        ).fetchone()
        assert row is not None
        assert secret_value.encode("utf-8") not in bytes(row["ciphertext"])
        assert row["credential_type"] == overrides.PROFILE_OVERRIDE_SECRET_TYPE
        assert extracted["extraction_id"] in row["account_label"]
    finally:
        connection.close()


def test_candidate_override_fails_closed_without_vault(hunter_db, monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    extracted = {
        "extraction_id": "profile-no-vault",
        "profile": v3.CandidateProfileExtract(professional_summary="Evidence summary").model_dump(),
    }
    with pytest.raises(RuntimeError, match="never saved in plaintext"):
        overrides.save_profile_override(extracted, "professional_summary", "Unsafe edit")


def test_reset_section_restores_master_resume_value(hunter_db, monkeypatch) -> None:
    extracted = _extracted(hunter_db, monkeypatch)
    overrides.save_profile_override(extracted, "professional_summary", "Temporary override")
    assert overrides.resolve_profile(extracted)["professional_summary"] == "Temporary override"

    overrides.reset_profile_section(extracted, "professional_summary")
    assert overrides.resolve_profile(extracted)["professional_summary"] == "HR analytics candidate."


def test_editor_only_supports_explicit_non_sensitive_resume_sections() -> None:
    assert set(overrides.EDITABLE_SECTIONS) == {
        "professional_summary",
        "contact",
        "education",
        "experience",
        "projects",
        "skills",
        "certifications",
        "languages",
    }
    forbidden = {"gender", "ethnicity", "veteran", "disability", "work_authorization"}
    assert not (forbidden & set(overrides.EDITABLE_SECTIONS))


def test_profile_workspace_v3_reuses_v1_layout_and_v2_promotion() -> None:
    source = Path("app/profile_workspace_v3.py").read_text(encoding="utf-8")
    assert "v1._render_profile_details" in source
    assert "v2._render_preview_controls" in source
    assert "resolve_profile" in source
    assert "Save encrypted profile edit" in source
    assert "Reset this section to Master Resume extraction" in source
    assert "Application defaults & self-ID" in source


def test_product_shell_preserves_v1_route_and_installs_v3_editor() -> None:
    source = Path("app/product_shell.py").read_text(encoding="utf-8")
    assert "from app import product_pages, profile_workspace_v1, resume_studio_page" in source
    assert "profile_workspace_v2, profile_workspace_v3" in source
    assert "profile_workspace_v1.render = profile_workspace_v3.render" in source
    assert '"profile": profile_workspace_v1.render' in source


def test_profile_editor_does_not_introduce_submission_authority() -> None:
    source = (Path("app/profile_workspace_v3.py").read_text(encoding="utf-8") + Path("app/profile_truth_overrides_v1.py").read_text(encoding="utf-8")).casefold()
    for forbidden in ("submit_application", "click_submit", "browser.submit", "playwright"):
        assert forbidden not in source
