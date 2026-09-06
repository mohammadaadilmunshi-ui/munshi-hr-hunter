from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app import application_plan_transport_v2 as transport
from app import database


def _plan(plan_id: str = "application-plan-1") -> dict[str, object]:
    snapshot = {
        "version": "munshi-application-plan-v2",
        "application_id": "application-1",
        "job": {"id": 42, "job_snapshot_digest": "d" * 64},
        "candidate_truth_binding": {"profile_digest": "a" * 64},
        "resume": {
            "artifact_id": "artifact-pdf-1",
            "artifact_sha256": "3" * 64,
        },
        "provider_policy": {"provider": "GREENHOUSE", "permitted": True},
        "expected_state": "READY_TO_APPLY",
        "executable": True,
        "submission_authority": False,
        "plan_id": plan_id,
        "idempotency_key": "plan-key",
    }
    digest_payload = {
        key: value
        for key, value in snapshot.items()
        if key not in {"plan_id", "idempotency_key", "plan_digest", "created_at"}
    }
    digest = hashlib.sha256(
        json.dumps(
            digest_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    snapshot["plan_digest"] = digest
    return {
        "plan_id": plan_id,
        "tenant_id": "default",
        "user_id": "local-owner",
        "application_id": "application-1",
        "provider": "GREENHOUSE",
        "plan_digest": digest,
        "snapshot": snapshot,
    }


def _wire_transport(hunter_db, monkeypatch: pytest.MonkeyPatch, plan: dict[str, object]) -> None:
    monkeypatch.setenv(transport.LIVE_HANDOFF_ENV, "true")
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", "synthetic-bridge-secret-123")
    monkeypatch.setattr(transport.application_plan_v2, "executable_plan", lambda _plan_id: plan)
    monkeypatch.setattr(
        transport,
        "current_owner",
        lambda _connection: SimpleNamespace(tenant_id="default", user_id="local-owner"),
    )

    def simple_schema(connection=None):
        owns = connection is None
        connection = connection or database.get_connection()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS apply_plan_handoffs_v2(
                    handoff_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    plan_id TEXT NOT NULL,
                    application_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    transport_version TEXT NOT NULL,
                    plan_digest TEXT NOT NULL,
                    body_sha256 TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    accepted_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(tenant_id,user_id,idempotency_key),
                    UNIQUE(tenant_id,user_id,plan_id)
                )"""
            )
            if owns:
                connection.commit()
        finally:
            if owns:
                connection.close()

    monkeypatch.setattr(transport, "ensure_schema", simple_schema)


def test_plan_transport_gate_is_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(transport.LIVE_HANDOFF_ENV, raising=False)
    assert transport.live_handoff_enabled() is False


def test_signed_transport_is_exact_idempotent_and_inert(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    _wire_transport(hunter_db, monkeypatch, plan)

    first = transport.create_signed_plan_transport(
        plan_id="application-plan-1",
        idempotency_key="handoff-key",
        timestamp=1000,
    )
    replay = transport.create_signed_plan_transport(
        plan_id="application-plan-1",
        idempotency_key="handoff-key",
        timestamp=1000,
    )

    assert first.body == replay.body
    assert first.headers == replay.headers
    envelope = json.loads(first.body)
    assert envelope["state"] == "READY_TO_APPLY"
    assert envelope["plan_id"] == "application-plan-1"
    assert envelope["plan_digest"] == plan["plan_digest"]
    assert envelope["submission_authority"] is False
    assert envelope["plan"]["submission_authority"] is False
    assert envelope["handoff_id"] == first.headers["X-Munshi-Event-Id"]
    assert first.headers["X-Munshi-Content-SHA256"] == hashlib.sha256(first.body).hexdigest()

    connection = database.get_connection()
    try:
        row = connection.execute("SELECT * FROM apply_plan_handoffs_v2").fetchone()
        assert row is not None
        assert row["state"] == "READY_TO_APPLY"
        assert row["accepted_at"] is None
        # Sender-side transport creation is formatting + ledger only.
        assert int(connection.execute("SELECT already_applied FROM jobs LIMIT 1").fetchone()[0] or 0) == 0
    finally:
        connection.close()


def test_handoff_idempotency_key_cannot_move_to_another_plan(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_plan = _plan("application-plan-1")
    _wire_transport(hunter_db, monkeypatch, first_plan)
    transport.create_signed_plan_transport(
        plan_id="application-plan-1",
        idempotency_key="handoff-key",
        timestamp=1000,
    )

    second_plan = _plan("application-plan-2")
    monkeypatch.setattr(
        transport.application_plan_v2,
        "executable_plan",
        lambda _plan_id: second_plan,
    )
    with pytest.raises(ValueError, match="another Application Plan handoff"):
        transport.create_signed_plan_transport(
            plan_id="application-plan-2",
            idempotency_key="handoff-key",
            timestamp=1001,
        )


def test_plan_acceptance_ack_is_correlated_and_not_submission(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = _plan()
    _wire_transport(hunter_db, monkeypatch, plan)
    signed = transport.create_signed_plan_transport(
        plan_id="application-plan-1",
        idempotency_key="handoff-key",
        timestamp=1000,
    )
    handoff_id = str(json.loads(signed.body)["handoff_id"])

    accepted = transport.record_plan_accepted(
        handoff_id=handoff_id,
        plan_id="application-plan-1",
        plan_digest=str(plan["plan_digest"]),
    )
    assert accepted == {
        "handoff_id": handoff_id,
        "plan_id": "application-plan-1",
        "state": "PLAN_ACCEPTED",
        "submission_authority": False,
    }

    connection = database.get_connection()
    try:
        row = connection.execute(
            "SELECT state,accepted_at FROM apply_plan_handoffs_v2 WHERE handoff_id=?",
            (handoff_id,),
        ).fetchone()
        assert row["state"] == "PLAN_ACCEPTED"
        assert row["accepted_at"] is not None
    finally:
        connection.close()

    with pytest.raises(PermissionError, match="does not match"):
        transport.record_plan_accepted(
            handoff_id=handoff_id,
            plan_id="application-plan-1",
            plan_digest="f" * 64,
        )
