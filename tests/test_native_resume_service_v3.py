from __future__ import annotations

import base64
import json
import os
import sys
import types

import pytest

from app import database
from app import native_resume_service_v3 as v3


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
