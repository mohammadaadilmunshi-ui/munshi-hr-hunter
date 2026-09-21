from __future__ import annotations

import sqlite3

import pytest

from app import answer_brain_v2
from app import application_field_intelligence_v1 as field_intelligence
from app import native_resume_service_v5
from app import prepare_runtime_worker


def _snapshot() -> dict:
    return {
        "tenant_id": "tenant-test",
        "user_id": "user-test",
        "source_extraction_id": "extract-test",
        "profile_revision": 7,
        "profile_digest": "a" * 64,
        "facts": [
            {
                "fact_id": "fact-location",
                "key": "contact.location",
                "category": "contact",
                "trust_level": "USER_CONFIRMED",
                "source": "master_resume",
                "value": "Palmyra, NJ",
                "protected": False,
            }
        ],
    }


def test_legacy_location_projection_preserves_real_source_fact(monkeypatch) -> None:
    snapshot = _snapshot()
    monkeypatch.setattr(
        field_intelligence.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: snapshot,
    )

    original_project = field_intelligence._project

    def project(value, projection):
        if projection == "location_city":
            return "Palmyra"
        return original_project(value, projection)

    monkeypatch.setattr(field_intelligence, "_project", project)

    result = field_intelligence.resolve_profile_projection(
        profile_fact_key="contact.city",
        projection="identity",
    )

    assert result["status"] == "ANSWERED"
    assert result["answer"]["canonical_answer"] == "Palmyra"
    assert result["answer"]["fact_key"] == "contact.city"
    assert result["answer"]["source_fact_key"] == "contact.location"


def test_projection_answer_binds_to_existing_legacy_source_fact(monkeypatch) -> None:
    snapshot = _snapshot()
    legacy_fact = dict(snapshot["facts"][0])

    monkeypatch.setattr(
        answer_brain_v2.truth_binding,
        "current_candidate_profile_snapshot",
        lambda: snapshot,
    )
    monkeypatch.setattr(
        answer_brain_v2.truth_binding,
        "profile_fact",
        lambda _snapshot, key: legacy_fact
        if str(key).casefold() == "contact.location"
        else None,
    )
    monkeypatch.setattr(
        answer_brain_v2.truth_binding,
        "binding_matches_snapshot",
        lambda binding, current: (
            binding.get("source_extraction_id") == current["source_extraction_id"]
            and int(binding.get("profile_revision") or 0)
            == int(current["profile_revision"])
            and binding.get("profile_digest") == current["profile_digest"]
        ),
    )

    monkeypatch.setattr(
        field_intelligence,
        "resolve_profile_projection",
        lambda **_kwargs: {
            "status": "ANSWERED",
            "answer": {
                "canonical_answer": "Palmyra",
                "source_fact_key": "contact.location",
                "candidate_truth_binding": {
                    "source_extraction_id": snapshot["source_extraction_id"],
                    "profile_revision": snapshot["profile_revision"],
                    "profile_digest": snapshot["profile_digest"],
                },
            },
        },
    )

    saved = {}

    def fake_save(**kwargs):
        saved.update(kwargs)
        return "answer-test"

    monkeypatch.setattr(answer_brain_v2, "_save_profile_answer_atomic", fake_save)

    answer_id = answer_brain_v2.save_profile_projection_answer(
        question_family="candidate_fact",
        question_key="contact.city",
        profile_fact_key="contact.city",
        projection="identity",
        autofill_allowed=True,
    )

    assert answer_id == "answer-test"
    assert saved["answer"] == "Palmyra"
    assert saved["profile_fact_key"] == "contact.location"
    assert saved["snapshot"]["profile_digest"] == snapshot["profile_digest"]


def test_prepare_runtime_defers_thrown_sqlite_writer_lock(monkeypatch) -> None:
    monkeypatch.setattr(
        prepare_runtime_worker,
        "_source_runner_status",
        lambda: {"state": "idle"},
    )
    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda **_kwargs: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    result = prepare_runtime_worker.run_once(limit=1)

    assert result["success"] is True
    assert result["status"] == "deferred"
    assert result["reason"] == "sqlite_writer_busy"
    assert result["errors"] == []
    assert result["submission_authority"] is False


def test_prepare_runtime_defers_only_all_busy_operation_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        prepare_runtime_worker,
        "_source_runner_status",
        lambda: {"state": "idle"},
    )
    monkeypatch.setattr(
        prepare_runtime_worker.autonomous_prepare_v1,
        "drive_open_operations",
        lambda **_kwargs: {
            "success": False,
            "operation_count": 1,
            "results": [],
            "errors": [
                {
                    "operation_id": "prepare-op-test",
                    "error_type": "OperationalError",
                    "error": "database table is locked",
                }
            ],
            "submission_authority": False,
        },
    )

    result = prepare_runtime_worker.run_once(limit=1)

    assert result["success"] is True
    assert result["status"] == "deferred"
    assert result["reason"] == "sqlite_writer_busy"
    assert result["operation_count"] == 1
    assert result["deferred_operation_ids"] == ["prepare-op-test"]


def test_resume_writer_retries_one_malformed_json_response(monkeypatch) -> None:
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("The model response was not valid resume JSON.")
        return {"contact": {}, "summary": "ok"}, "response-2", "model-test"

    monkeypatch.setattr(
        native_resume_service_v5.v4.v2,
        "_call_openai_v2",
        fake_call,
    )

    payload, response_id, model, calls_used, retry_payload = (
        native_resume_service_v5._call_writer_with_json_retry(
            prompt_payload={
                "task": "generate_resume",
                "instruction": "Tailor truthfully.",
            },
            config={"max_calls_per_generation": 2},
            rewrite_mode="medium",
        )
    )

    assert payload["summary"] == "ok"
    assert response_id == "response-2"
    assert model == "model-test"
    assert calls_used == 2
    assert len(calls) == 2
    assert retry_payload is not None
    assert retry_payload["task"] == "repair_resume_json_transport"
    assert "exactly one complete JSON object" in retry_payload["instruction"]


def test_resume_writer_does_not_exceed_single_call_budget(monkeypatch) -> None:
    calls = []

    def fake_call(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("The model response was not valid resume JSON.")

    monkeypatch.setattr(
        native_resume_service_v5.v4.v2,
        "_call_openai_v2",
        fake_call,
    )

    with pytest.raises(RuntimeError, match="valid resume JSON"):
        native_resume_service_v5._call_writer_with_json_retry(
            prompt_payload={"task": "generate_resume", "instruction": "Tailor."},
            config={"max_calls_per_generation": 1},
            rewrite_mode="medium",
        )

    assert len(calls) == 1

def test_staging_deploy_safety_override_is_last_compose_layer() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = (root / "deploy" / "netcup" / "deploy_staging_release.sh").read_text()

    assert 'STAGING_SAFETY_OVERRIDE="$STAGING_ROOT/staging.safety.override.yaml"' in script
    assert 'STAGING_SAFETY_OVERRIDE=ENFORCED' in script
    assert 'compose+=(-f "$STAGING_OPERATIONAL_OVERRIDE")' in script
    assert 'compose+=(-f "$STAGING_SAFETY_OVERRIDE")' in script
    assert script.index('compose+=(-f "$STAGING_OPERATIONAL_OVERRIDE")') < script.index(
        'compose+=(-f "$STAGING_SAFETY_OVERRIDE")'
    )

    for token in (
        'HUNTER_ENABLE_TELEGRAM: "false"',
        'HUNTER_ENABLE_DISCOVERY_SCHEDULER: "false"',
        'HUNTER_ENABLE_COORDINATOR: "false"',
        'TELEGRAM_ENABLED: "false"',
        'DISCOVERY_ENABLED: "false"',
        'SCHEDULER_ENABLED: "false"',
        'COORDINATOR_ENABLED: "false"',
        'PRODUCTION_CALLBACKS_ENABLED: "false"',
        'PRODUCTION_STATE_IMPORTED: "false"',
        'CLOUD_SHADOW_MODE: "true"',
    ):
        assert token in script

def test_staging_deploy_requires_guarded_maintenance_repair() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    deploy = (root / "deploy" / "netcup" / "deploy_staging_release.sh").read_text()
    helper = (root / "deploy" / "netcup" / "ensure_staging_maintenance_mode.sh").read_text()

    assert 'STAGING_MAINTENANCE_HELPER=' in deploy
    assert '=== ENSURE STAGING MAINTENANCE MODE ===' in deploy
    assert '"$STAGING_MAINTENANCE_HELPER"' in deploy

    for token in (
        'HUNTER_ENABLE_DISCOVERY_SCHEDULER=false',
        'HUNTER_ENABLE_COORDINATOR=false',
        'HUNTER_ENABLE_TELEGRAM=false',
        'PRODUCTION_CALLBACKS_ENABLED=false',
        'PRODUCTION_STATE_IMPORTED=false',
    ):
        assert token in helper

    assert 'payload["maintenance_mode"] = True' in helper
    assert 'STAGING_MAINTENANCE_REPAIR=PASS' in helper
