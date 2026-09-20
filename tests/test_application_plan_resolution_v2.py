from __future__ import annotations

from types import SimpleNamespace

from app import application_plan_resolution_v2 as resolution
from app import candidate_knowledge_graph_v1 as graph
from app import database


def test_runtime_resolution_orchestrates_exact_replacement_and_delivery(monkeypatch) -> None:
    replacement = {
        "plan_id": "replacement-plan-1",
        "plan_digest": "a" * 64,
        "inputs": {
            "answer_requirements": [
                {
                    "question_key": "contact.postal_code",
                    "question_family": "candidate_fact",
                    "conditions": {"profile_projection": "identity"},
                }
            ]
        },
    }
    calls: list[tuple[str, dict]] = []

    def reprepare(**kwargs):
        calls.append(("reprepare", kwargs))
        return replacement

    def deliver(**kwargs):
        calls.append(("deliver", kwargs))
        return SimpleNamespace(
            handoff_id="handoff-1",
            plan_id="replacement-plan-1",
            plan_digest="a" * 64,
            state="PLAN_ACCEPTED",
            replayed=False,
            submission_authority=False,
        )

    monkeypatch.setattr(
        resolution.application_plan_v2, "reprepare_runtime_needs_input_plan", reprepare
    )
    monkeypatch.setattr(
        resolution.application_plan_delivery_v2, "deliver_application_plan", deliver
    )
    promoted = []
    monkeypatch.setattr(
        resolution.answer_brain_v2,
        "save_answer",
        lambda **kwargs: promoted.append(kwargs) or "answer-1",
    )
    result = resolution.resolve_runtime_needs_input(
        prior_plan_id="prior-plan-1",
        expected_prior_plan_digest="b" * 64,
        revision_idempotency_key="revision-key-1",
        runtime_context={
            "session_id": "session-1",
            "checkpoint_id": "checkpoint-1",
            "browser_form_digest": "c" * 64,
            "unresolved_questions": [],
        },
        answer_resolutions=[
            {"question_key": "contact.postal_code", "canonical_answer": "08065"}
        ],
    )
    assert result["replacement_plan_id"] == "replacement-plan-1"
    assert result["state"] == "PLAN_ACCEPTED"
    assert result["submission_authority"] is False
    assert promoted == []
    assert calls[0][0] == "reprepare"
    assert calls[1][0] == "deliver"
    assert calls[1][1]["idempotency_key"].startswith(
        "runtime-resolution-handoff-"
    )
    assert "canonical_answer" not in str(result)


def test_verified_runtime_user_answer_promotes_to_canonical_knowledge_only_after_gate(hunter_db) -> None:
    stored = resolution._promote_runtime_resolution_to_knowledge(
        requirement={
            "universal_concept_id": "contact.region",
            "question_family": "candidate_fact",
        },
        question_key="unknown.runtime.region",
        canonical_answer="Pennsylvania",
        verified_plan_id="application-plan-verified-1",
    )
    assert stored is not None
    assert stored["concept_id"] == "contact.region"
    assert stored["value"] == "Pennsylvania"
    assert stored["source_type"] == "USER_DASHBOARD"
    assert stored["trust_level"] == "USER_CONFIRMED"
    resolved = graph.resolve_fact("contact.region")
    assert resolved["status"] == "ANSWERED"
    assert resolved["fact"]["value"] == "Pennsylvania"

    connection = database.get_connection()
    try:
        row = connection.execute(
            """SELECT evidence_text,metadata_json FROM candidate_knowledge_evidence
               WHERE fact_id=?""",
            (stored["fact_id"],),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None
    assert row["evidence_text"] is None
    assert "Pennsylvania" not in str(row["metadata_json"])
    assert "PRODUCTION_SUBMISSION_RECEIPT_VERIFIED" in str(row["metadata_json"])


def test_verified_runtime_protected_or_job_specific_answer_never_enters_normal_knowledge(hunter_db) -> None:
    protected = resolution._promote_runtime_resolution_to_knowledge(
        requirement={"universal_concept_id": "work.authorization.us"},
        question_key="work.auth",
        canonical_answer="Yes",
        verified_plan_id="application-plan-verified-2",
    )
    assert protected is None
    job_specific = resolution._promote_runtime_resolution_to_knowledge(
        requirement={"universal_concept_id": "motivation.why_company"},
        question_key="motivation.why_company",
        canonical_answer="Role-specific text",
        verified_plan_id="application-plan-verified-2",
    )
    assert job_specific is None
