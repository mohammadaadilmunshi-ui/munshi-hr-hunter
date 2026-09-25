from __future__ import annotations

from pathlib import Path

from app import application_field_intelligence_v1 as fields
from app import prepare_worker_adapters_v1c as prepare_v1c


ROOT = Path(__file__).resolve().parents[1]

CANONICAL_RUNTIME_FILES = (
    ROOT / "app" / "prepare_worker_adapters_v1a.py",
    ROOT / "app" / "prepare_worker_adapters_v1b.py",
    ROOT / "app" / "prepare_worker_adapters_v1c.py",
    ROOT / "app" / "product_application_flow_v2.py",
    ROOT / "app" / "application_workspace_page.py",
    ROOT / "app" / "application_plan_v2.py",
)

FORBIDDEN_ACCEPTANCE_LITERALS = (
    "MUNSHI_NEOMAX",
    "neomax.jobs",
    "MUNSHI_PREPARE_PIN_RESUME_VERSION_ID",
)


def test_canonical_runtime_has_no_acceptance_job_literals() -> None:
    violations: list[str] = []
    for path in CANONICAL_RUNTIME_FILES:
        content = path.read_text(encoding="utf-8")
        for literal in FORBIDDEN_ACCEPTANCE_LITERALS:
            if literal in content:
                violations.append(f"{path.relative_to(ROOT)}:{literal}")
    assert violations == []


def test_product_path_never_drives_prepare_operation_manually() -> None:
    for relative in (
        "app/product_application_flow_v2.py",
        "app/application_workspace_page.py",
        "app/product_pages.py",
        "app/api.py",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "drive_operation_once(" not in content, relative


def test_address_labels_map_to_provider_neutral_candidate_truth_keys() -> None:
    cases = (
        ({"label": "Address"}, "contact.address_line1", "contact.address_line1"),
        ({"label": "Street Address"}, "contact.address_line1", "contact.address_line1"),
        ({"label": "Address Line 2"}, "contact.address_line2", "contact.address_line2"),
        ({"label": "City"}, "contact.city", "contact.city"),
        ({"label": "State"}, "contact.region", "contact.region"),
        ({"label": "Zip Code"}, "contact.postal_code", "contact.postal_code"),
        ({"label": "Country"}, "contact.country", "contact.country"),
        ({"semantic_type": "POSTAL_CODE"}, "contact.postal_code", "contact.postal_code"),
    )
    for metadata, question_key, profile_fact_key in cases:
        requirement = fields.requirement_from_field(metadata)
        assert requirement is not None
        assert requirement["question_key"] == question_key
        assert requirement["profile_fact_key"] == profile_fact_key
        assert requirement["sensitivity_class"] == "NORMAL"


def test_structured_address_fact_is_used_without_provider_specific_logic(monkeypatch) -> None:
    monkeypatch.setattr(
        fields.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: {
            "profile_revision": 7,
            "profile_digest": "a" * 64,
            "source_extraction_id": "profile-source",
            "facts": [
                {
                    "key": "contact.postal_code",
                    "value": "08065",
                    "protected": False,
                    "trust_level": "USER_CONFIRMED",
                    "source": "candidate-profile-overrides-v1:r2",
                }
            ],
        },
    )
    monkeypatch.setattr(
        fields.truth_binding,
        "public_binding_state",
        lambda snapshot: {
            "profile_revision": snapshot["profile_revision"],
            "profile_digest": snapshot["profile_digest"],
        },
    )

    resolved = fields.resolve_profile_projection(
        profile_fact_key="contact.postal_code",
        projection="identity",
    )

    assert resolved["status"] == "ANSWERED"
    assert resolved["answer"]["canonical_answer"] == "08065"
    assert resolved["answer"]["autofill_allowed"] is True


def test_runtime_supersession_uses_exact_fresh_replacement(monkeypatch) -> None:
    operation = {
        "application_id": "application-1",
        "job_id": 22,
    }
    pending = {
        "output": {
            "plan_id": "plan-prior",
        }
    }
    preflight = {
        "preflight_package_id": "preflight-1",
        "resume_version_id": "resume-1",
        "resume_pdf_sha256": "b" * 64,
    }

    monkeypatch.setattr(
        prepare_v1c.application_plan_v2,
        "runtime_plan_replacement",
        lambda plan_id: {
            "prior_plan_id": plan_id,
            "replacement_plan_id": "plan-replacement",
        },
    )
    monkeypatch.setattr(
        prepare_v1c.application_plan_v2,
        "get_application_plan",
        lambda plan_id: {
            "plan_id": plan_id,
            "idempotency_key": "runtime-revision-key",
            "application_id": "application-1",
            "job_id": 22,
            "preflight_package_id": "preflight-1",
            "status": "READY_TO_APPLY",
            "executable": True,
            "submission_authority": False,
            "snapshot": {
                "resume": {
                    "version_id": "resume-1",
                    "artifact_sha256": "b" * 64,
                }
            },
        },
    )
    monkeypatch.setattr(
        prepare_v1c.application_plan_v2,
        "application_plan_freshness",
        lambda plan_id: {"fresh": plan_id == "plan-replacement"},
    )

    replacement, prior = prepare_v1c._runtime_replacement_plan(
        operation=operation,
        pending_task=pending,
        preflight=preflight,
    )

    assert prior == "plan-prior"
    assert replacement is not None
    assert replacement["plan_id"] == "plan-replacement"
