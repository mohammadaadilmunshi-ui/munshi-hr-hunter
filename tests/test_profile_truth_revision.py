from __future__ import annotations

import base64
import json
import os

import pytest

from app import native_resume_service_v3 as v3
from app import profile_truth_overrides_v1 as overrides
from app import resume_profile_details_v31 as details
from app.secure_vault import store_secret


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _extracted(hunter_db, monkeypatch) -> dict:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = v3.save_confirmed_source(
        content_text="Example Candidate\nSUMMARY\nEvidence summary.\nSKILLS\nExcel | Power BI",
        label="Master Resume",
        source_kind="pasted_text",
    )
    extracted = v3._persist_profile(
        source=source,
        profile=v3.CandidateProfileExtract(
            professional_summary="Evidence summary.",
            contact=v3.ContactProfile(full_name="Example Candidate"),
        ),
        model="test-model",
        response_id="resp-revision-strengthening",
    )
    return v3.confirm_profile_extract(extracted["extraction_id"])


def test_resetting_last_override_preserves_monotonic_encrypted_revision(hunter_db, monkeypatch) -> None:
    extracted = _extracted(hunter_db, monkeypatch)

    saved = overrides.save_profile_override(
        extracted,
        "professional_summary",
        "Candidate-confirmed summary.",
    )
    assert saved.revision == 1

    reset = overrides.reset_profile_section(extracted, "professional_summary")
    assert reset.revision == 2
    assert reset.sections == {}
    assert overrides.resolve_profile(extracted)["professional_summary"] == "Evidence summary."

    reloaded = overrides.load_profile_overrides(extracted["extraction_id"])
    assert reloaded.revision == 2
    assert reloaded.sections == {}

    saved_again = overrides.save_profile_override(
        extracted,
        "professional_summary",
        "Second candidate-confirmed summary.",
    )
    assert saved_again.revision == 3


def test_reset_all_preserves_revision_instead_of_reverting_to_zero(hunter_db, monkeypatch) -> None:
    extracted = _extracted(hunter_db, monkeypatch)
    overrides.save_profile_override(extracted, "professional_summary", "Updated summary")
    overrides.save_profile_override(
        extracted,
        "languages",
        ["English"],
    )
    before = overrides.load_profile_overrides(extracted["extraction_id"])
    assert before.revision == 2
    assert set(before.sections) == {"professional_summary", "languages"}

    assert overrides.reset_all_profile_overrides(extracted) is True
    after = overrides.load_profile_overrides(extracted["extraction_id"])
    assert after.revision == 3
    assert after.sections == {}
    assert overrides.reset_all_profile_overrides(extracted) is False
    assert overrides.load_profile_overrides(extracted["extraction_id"]).revision == 3


def test_candidate_entered_details_revision_increments_on_every_confirmed_save(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())

    details.save_candidate_profile_details(
        {
            "work_authorization_country": "United States",
            "visa_or_permit": "Example visa",
            "work_modes": ["Hybrid"],
        }
    )
    first = details.load_candidate_profile_details_envelope()
    assert first.revision == 1
    assert first.values.work_modes == ["Hybrid"]

    details.save_candidate_profile_details(
        {
            "work_authorization_country": "United States",
            "visa_or_permit": "Example visa",
            "work_modes": ["Hybrid", "On-site"],
        }
    )
    second = details.load_candidate_profile_details_envelope()
    assert second.revision == 2
    assert second.values.work_modes == ["Hybrid", "On-site"]
    assert second.updated_at


def test_legacy_candidate_details_payload_is_read_as_revision_zero_then_upgraded(hunter_db, monkeypatch) -> None:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())

    legacy = details.CandidateProvidedProfileDetails(
        work_authorization_country="United States",
        work_modes=["Remote"],
    )
    store_secret(
        details.PROFILE_DETAILS_SECRET_TYPE,
        json.dumps(legacy.model_dump(), ensure_ascii=False, sort_keys=True),
        account_label=details._owner_label(),
    )

    loaded = details.load_candidate_profile_details_envelope()
    assert loaded.revision == 0
    assert loaded.values.work_modes == ["Remote"]

    details.save_candidate_profile_details(loaded.values.model_dump())
    upgraded = details.load_candidate_profile_details_envelope()
    assert upgraded.revision == 1
    assert upgraded.schema_version == details.PROFILE_DETAILS_ENVELOPE_SCHEMA
