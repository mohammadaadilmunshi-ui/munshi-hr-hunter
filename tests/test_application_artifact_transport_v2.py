from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app import application_artifact_transport_v2 as bridge

SECRET = "phase1cb-test-bridge-secret"
ARTIFACT = b"%PDF-1.4\n% synthetic artifact\n%%EOF\n"
ARTIFACT_SHA = hashlib.sha256(ARTIFACT).hexdigest()


def _plan():
    snapshot = {
        "application_id": "application-1",
        "owner_binding": {"tenant_id": "tenant-a", "user_id": "member-a"},
        "resume": {
            "artifact_id": "artifact-1",
            "artifact_reference": "hunter-native-resume://resume-1/pdf",
            "artifact_sha256": ARTIFACT_SHA,
        },
        "submission_authority": False,
    }
    return {
        "tenant_id": "tenant-a", "user_id": "member-a", "application_id": "application-1",
        "plan_id": "plan-1", "plan_digest": "a" * 64, "snapshot": snapshot, "executable": True,
    }


def _request(purpose: str, *, timestamp: int = 1000, **changes):
    payload = {
        "version": bridge.REQUEST_VERSION, "request_id": "request-1", "purpose": purpose,
        "tenant_id": "tenant-a", "user_id": "member-a", "application_id": "application-1",
        "plan_id": "plan-1", "plan_digest": "a" * 64, "artifact_id": "artifact-1",
        "artifact_reference": "hunter-native-resume://resume-1/pdf", "artifact_sha256": ARTIFACT_SHA,
    }
    payload.update(changes)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(SECRET.encode(), f"{payload['request_id']}.{timestamp}.{digest}".encode(), hashlib.sha256).hexdigest()
    return body, {
        "X-Munshi-Event-Id": payload["request_id"], "X-Munshi-Timestamp": str(timestamp),
        "X-Munshi-Content-SHA256": digest, "X-Munshi-Signature": f"sha256={signature}",
    }


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", SECRET)
    monkeypatch.setattr(bridge.application_plan_v2, "executable_plan", lambda _plan_id: _plan())
    monkeypatch.setattr(bridge.native_resume_artifact_v5, "artifact_bytes", lambda _artifact_id: ARTIFACT)


def _verify_response(body: bytes, headers: dict[str, str], purpose: str):
    digest = hashlib.sha256(body).hexdigest()
    assert headers["X-Munshi-Response-SHA256"] == digest
    expected = hmac.new(SECRET.encode(), f"request-1.{purpose}.{digest}.{'a' * 64}".encode(), hashlib.sha256).hexdigest()
    assert headers["X-Munshi-Response-Signature"] == f"sha256={expected}"


def test_current_plan_is_fresh_and_submission_authority_false():
    body, headers = _request(bridge.PURPOSE_PLAN_CURRENT)
    response, response_headers = bridge.current_plan_response(body, headers, now=1000)
    payload = json.loads(response)
    assert payload["fresh"] is True
    assert payload["plan_digest"] == "a" * 64
    assert payload["submission_authority"] is False
    _verify_response(response, response_headers, bridge.PURPOSE_PLAN_CURRENT)


def test_artifact_bytes_are_exact_and_response_signed():
    body, headers = _request(bridge.PURPOSE_ARTIFACT_BYTES)
    response, response_headers = bridge.artifact_response(body, headers, now=1000)
    assert response == ARTIFACT
    assert response_headers["X-Munshi-Artifact-SHA256"] == ARTIFACT_SHA
    assert response_headers["X-Munshi-Submission-Authority"] == "false"
    _verify_response(response, response_headers, bridge.PURPOSE_ARTIFACT_BYTES)


def test_tamper_stale_and_binding_conflicts_fail_closed():
    body, headers = _request(bridge.PURPOSE_PLAN_CURRENT)
    tampered = body.replace(b"application-1", b"application-x")
    with pytest.raises(PermissionError):
        bridge.current_plan_response(tampered, headers, now=1000)
    stale_body, stale_headers = _request(bridge.PURPOSE_PLAN_CURRENT, timestamp=1)
    with pytest.raises(PermissionError, match="stale"):
        bridge.current_plan_response(stale_body, stale_headers, now=1000)
    conflict_body, conflict_headers = _request(bridge.PURPOSE_PLAN_CURRENT, artifact_id="different-artifact")
    with pytest.raises(PermissionError, match="immutable plan"):
        bridge.current_plan_response(conflict_body, conflict_headers, now=1000)


def test_artifact_digest_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(bridge.native_resume_artifact_v5, "artifact_bytes", lambda _artifact_id: b"different bytes")
    body, headers = _request(bridge.PURPOSE_ARTIFACT_BYTES)
    with pytest.raises(RuntimeError, match="digest"):
        bridge.artifact_response(body, headers, now=1000)
