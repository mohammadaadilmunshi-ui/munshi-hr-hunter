from __future__ import annotations

import base64
import os
import sys
import types

import pytest

from app import database
from app import native_resume_service_v3 as v3
from app import resume_profile_details_v31 as v31
from app import resume_studio_source_workspace_v32 as v32


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def test_fragmented_pdf_text_is_reflowed_into_coherent_resume_lines(monkeypatch) -> None:
    fragmented = """Mohammad\n\nAadil\n\nVasim\n\nMunshi\n\nPalmyra,\n\nNJ\n\nSUMMARY\n\nMaster's\n\ncandidate\n\nin\n\nHuman\n\nResource\n\nAnalytics\n\nwith\n\nHR\n\noperations.\n\nSKILLS\n\nPower\n\nBI\n\nExcel\n"""

    class FakePage:
        def extract_text(self, extraction_mode=None):
            assert extraction_mode == "layout"
            return fragmented

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

        def __init__(self, _stream, strict=False):
            assert strict is False

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))
    text, kind = v31.extract_uploaded_source("resume.pdf", b"synthetic-pdf")

    assert kind == "text_upload"
    assert "Mohammad Aadil Vasim Munshi Palmyra, NJ" in text
    assert "SUMMARY" in text
    assert "Master's candidate in Human Resource Analytics with HR operations." in text
    assert "SKILLS" in text
    assert "Power BI Excel" in text
    assert "\nAadil\n" not in text


def test_non_fragmented_pdf_keeps_normal_line_structure(monkeypatch) -> None:
    normal = "SUMMARY\nPeople Analytics candidate with Power BI.\n\nEXPERIENCE\nHR Analyst\nExample Co"

    class FakePage:
        def extract_text(self, extraction_mode=None):
            return normal

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

        def __init__(self, _stream, strict=False):
            pass

    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=FakeReader))
    text, _ = v31.extract_uploaded_source("resume.pdf", b"synthetic-pdf")
    assert "SUMMARY\nPeople Analytics candidate with Power BI." in text
    assert "EXPERIENCE\nHR Analyst\nExample Co" in text


def test_sensitive_candidate_details_are_not_part_of_llm_extract_schema() -> None:
    llm_schema = v3.CandidateProfileExtract.model_json_schema()
    text = str(llm_schema).casefold()
    for forbidden in ("gender", "ethnicity", "race", "disability", "veteran"):
        assert forbidden not in text

    candidate_schema = str(v31.CandidateProvidedProfileDetails.model_json_schema()).casefold()
    assert "gender" in candidate_schema
    assert "ethnicity" in candidate_schema
    assert "disability" in candidate_schema
    assert "veteran" in candidate_schema


def test_candidate_profile_details_refuse_plaintext_fallback(hunter_db, monkeypatch) -> None:
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    with pytest.raises(RuntimeError, match="never saved to plaintext"):
        v31.save_candidate_profile_details({"gender": "Candidate entered"})


def test_candidate_profile_details_round_trip_only_through_aes_gcm_vault(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())

    saved = v31.save_candidate_profile_details(
        {
            "work_authorization_country": "United States",
            "visa_or_permit": "F-1",
            "sponsorship_required": True,
            "willing_to_relocate": True,
            "work_modes": ["Hybrid", "On-site"],
            "gender": "Candidate entered",
            "ethnicity": "Candidate entered",
            "veteran": False,
            "disability": False,
        }
    )
    assert saved["visa_or_permit"] == "F-1"
    assert saved["sponsorship_required"] is True

    loaded = v31.load_candidate_profile_details()
    assert loaded["work_authorization_country"] == "United States"
    assert loaded["work_modes"] == ["Hybrid", "On-site"]
    assert loaded["veteran"] is False

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT ciphertext FROM credential_secret WHERE credential_type=?",
            (v31.PROFILE_DETAILS_SECRET_TYPE,),
        ).fetchone()
        assert row is not None
        ciphertext = bytes(row["ciphertext"])
        assert b"Candidate entered" not in ciphertext
        assert b"F-1" not in ciphertext
    finally:
        connection.close()


def test_master_source_save_defers_widget_bound_state_changes_until_next_render() -> None:
    state = {
        "native_resume_v31_source_draft": "candidate-reviewed draft",
        "native_resume_v31_source_label": "Before save.pdf",
        "native_resume_v31_source_kind": "text_upload",
        "native_resume_v31_truth_confirm": True,
    }

    v32.mark_source_widget_refresh_pending(state)

    # The save-success path must not mutate keys already owned by instantiated
    # widgets during the current render pass.
    assert state["native_resume_v31_source_draft"] == "candidate-reviewed draft"
    assert state["native_resume_v31_source_label"] == "Before save.pdf"
    assert state["native_resume_v31_truth_confirm"] is True

    saved_source = {
        "content_text": "persisted canonical source",
        "label": "Saved master.pdf",
        "source_kind": "text_upload",
    }
    assert v32.apply_pending_source_widget_state(state, saved_source) is True

    # On the next pass, before widgets exist, the saved source becomes the
    # canonical widget state and the truth-confirmation box is safely cleared.
    assert state["native_resume_v31_source_draft"] == "persisted canonical source"
    assert state["native_resume_v31_source_label"] == "Saved master.pdf"
    assert state["native_resume_v31_source_kind"] == "text_upload"
    assert state["native_resume_v31_truth_confirm"] is False


def test_master_source_pending_refresh_is_consumed_only_once() -> None:
    state = {}
    v32.mark_source_widget_refresh_pending(state)
    assert v32.apply_pending_source_widget_state(state, {"content_text": "one"}) is True
    state["native_resume_v31_source_draft"] = "manual next edit"
    assert v32.apply_pending_source_widget_state(state, {"content_text": "two"}) is False
    assert state["native_resume_v31_source_draft"] == "manual next edit"
