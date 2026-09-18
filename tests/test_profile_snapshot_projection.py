from __future__ import annotations

import base64
import os

import pytest

from app import native_resume_service_v3 as v3
from app import profile_snapshot_projection as projection
from app import profile_truth_overrides_v1 as overrides
from app import resume_profile_details_v31 as details
from app.cross_repo_contract import canonical_json


def _vault_key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


def _profile_extract(hunter_db, monkeypatch, *, confirm: bool = True) -> dict:
    pytest.importorskip("cryptography")
    monkeypatch.setenv("MUNSHI_VAULT_KEY", _vault_key())
    source = v3.save_confirmed_source(
        content_text=(
            "Example Candidate\n"
            "SUMMARY\nPeople analytics candidate.\n"
            "EXPERIENCE\nExample Co | HR Analyst\n"
            "SKILLS\nExcel | Power BI"
        ),
        label="Master Resume",
        source_kind="pasted_text",
    )
    extracted = v3._persist_profile(
        source=source,
        profile=v3.CandidateProfileExtract(
            professional_summary="People analytics candidate.",
            contact=v3.ContactProfile(
                full_name="Example Candidate",
                location="New Jersey",
                email="candidate@example.test",
            ),
            experience=[
                v3.ExperienceProfile(
                    employer="Example Co",
                    title="HR Analyst",
                    bullets=["Built a Power BI workforce dashboard."],
                )
            ],
            skills=[
                v3.SkillCategory(
                    category="Analytics",
                    skills=["Excel", "Power BI"],
                )
            ],
        ),
        model="test-model",
        response_id="resp-phase12-projection",
    )
    return v3.confirm_profile_extract(extracted["extraction_id"]) if confirm else extracted


def _fact(snapshot: dict, key: str) -> dict:
    for item in snapshot["facts"]:
        if item["key"] == key:
            return item
    raise AssertionError(f"Missing projected fact: {key}")


def test_projection_binds_exact_confirmed_evidence_and_section1_revisions(hunter_db, monkeypatch) -> None:
    extracted = _profile_extract(hunter_db, monkeypatch)
    overrides.save_profile_override(
        extracted,
        "professional_summary",
        "Candidate-confirmed people analytics summary.",
    )
    details.save_candidate_profile_details(
        {
            "work_authorization_country": "United States",
            "visa_or_permit": "Example protected visa value",
            "sponsorship_required": True,
            "willing_to_relocate": True,
            "work_modes": ["Hybrid", "On-site"],
            "gender": "Example protected self-ID value",
        }
    )

    snapshot = projection.build_candidate_profile_snapshot(extracted)

    assert snapshot["authority"] == "munshi-hr-hunter"
    assert snapshot["projection_mode"] == "READ_ONLY"
    assert snapshot["revision_scope"] == "SOURCE_EXTRACTION"
    assert snapshot["source_extraction_id"] == extracted["extraction_id"]
    assert snapshot["source_profile_sha256"] == extracted["profile_sha256"]
    assert snapshot["source_resume_sha256"] == extracted["source_sha256"]
    assert snapshot["override_revision"] == 1
    assert snapshot["candidate_details_revision"] == 1
    assert snapshot["profile_revision"] > 0

    summary = _fact(snapshot, "profile.professional_summary")
    assert summary["value"] == "Candidate-confirmed people analytics summary."
    assert summary["trust_level"] == "USER_CONFIRMED"
    assert summary["source"].startswith("candidate-profile-overrides-v1:r1")

    name = _fact(snapshot, "contact.full_name")
    assert name["value"] == "Example Candidate"
    assert name["trust_level"] == "DOCUMENT_CONFIRMED"

    work_modes = _fact(snapshot, "application_defaults.work_modes")
    assert work_modes["value"] == ["Hybrid", "On-site"]
    assert work_modes["trust_level"] == "USER_CONFIRMED"

    authorization = _fact(snapshot, "application_defaults.visa_or_permit")
    assert authorization["protected"] is True
    assert "value" not in authorization
    assert authorization["value_reference"].startswith("hunter-vault://")

    self_id = _fact(snapshot, "application_defaults.gender")
    assert self_id["protected"] is True
    assert "value" not in self_id

    serialized = canonical_json(snapshot)
    assert "Example protected visa value" not in serialized
    assert "Example protected self-ID value" not in serialized


def test_projection_revision_advances_for_details_override_and_reset_changes(hunter_db, monkeypatch) -> None:
    extracted = _profile_extract(hunter_db, monkeypatch)

    base = projection.build_candidate_profile_snapshot(extracted)

    details.save_candidate_profile_details({"work_modes": ["Remote"]})
    details_changed = projection.build_candidate_profile_snapshot(extracted)
    assert details_changed["candidate_details_revision"] == 1
    assert details_changed["profile_revision"] > base["profile_revision"]
    assert details_changed["profile_digest"] != base["profile_digest"]

    overrides.save_profile_override(extracted, "professional_summary", "Updated summary")
    override_changed = projection.build_candidate_profile_snapshot(extracted)
    assert override_changed["override_revision"] == 1
    assert override_changed["profile_revision"] > details_changed["profile_revision"]
    assert override_changed["profile_digest"] != details_changed["profile_digest"]

    overrides.reset_profile_section(extracted, "professional_summary")
    reset = projection.build_candidate_profile_snapshot(extracted)
    assert reset["override_revision"] == 2
    assert reset["profile_revision"] > override_changed["profile_revision"]
    assert _fact(reset, "profile.professional_summary")["value"] == "People analytics candidate."


def test_repeated_projection_of_unchanged_truth_has_same_content_digest(hunter_db, monkeypatch) -> None:
    extracted = _profile_extract(hunter_db, monkeypatch)
    first = projection.build_candidate_profile_snapshot(extracted)
    second = projection.build_candidate_profile_snapshot(extracted)
    assert first["profile_digest"] == second["profile_digest"]
    assert first["facts"] == second["facts"]


def test_projection_rejects_draft_wrong_owner_or_tampered_evidence(hunter_db, monkeypatch) -> None:
    draft = _profile_extract(hunter_db, monkeypatch, confirm=False)
    with pytest.raises(ValueError, match="confirmed"):
        projection.build_candidate_profile_snapshot(draft)

    confirmed = v3.confirm_profile_extract(draft["extraction_id"])
    wrong_owner = {**confirmed, "user_id": "different-user"}
    with pytest.raises(ValueError, match="owner"):
        projection.build_candidate_profile_snapshot(wrong_owner)

    tampered = {
        **confirmed,
        "profile": {
            **confirmed["profile"],
            "professional_summary": "Tampered after confirmation",
        },
    }
    with pytest.raises(RuntimeError, match="hash"):
        projection.build_candidate_profile_snapshot(tampered)


def test_projection_fails_closed_when_encrypted_truth_store_is_unavailable(hunter_db, monkeypatch) -> None:
    extracted = _profile_extract(hunter_db, monkeypatch)
    monkeypatch.delenv("MUNSHI_VAULT_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Encrypted Candidate Truth Profile"):
        projection.build_candidate_profile_snapshot(extracted)
