from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from app import application_artifact_transport_v2 as bridge

SECRET = "phase1d-cover-bridge-secret"
COVER = b"%PDF-1.4\n% cover\n%%EOF\n"
COVER_SHA = hashlib.sha256(COVER).hexdigest()
RESUME_SHA = "d" * 64


def plan():
    return {
        "tenant_id": "tenant-a",
        "user_id": "member-a",
        "application_id": "application-cover",
        "plan_id": "plan-cover",
        "plan_digest": "a" * 64,
        "snapshot": {
            "application_id": "application-cover",
            "owner_binding": {"tenant_id": "tenant-a", "user_id": "member-a"},
            "resume": {
                "artifact_id": "resume-artifact",
                "artifact_reference": "hunter-native-resume://resume/pdf",
                "artifact_sha256": RESUME_SHA,
            },
            "cover_letter": {
                "artifact_id": "cover-artifact",
                "artifact_reference": "hunter-cover-letter://cover-artifact/pdf",
                "artifact_sha256": COVER_SHA,
            },
            "submission_authority": False,
        },
        "executable": True,
    }


def req(purpose, binding):
    p = {
        "version": bridge.REQUEST_VERSION,
        "request_id": "request-cover",
        "purpose": purpose,
        "tenant_id": "tenant-a",
        "user_id": "member-a",
        "application_id": "application-cover",
        "plan_id": "plan-cover",
        "plan_digest": "a" * 64,
        "artifact_id": binding["artifact_id"],
        "artifact_reference": binding["artifact_reference"],
        "artifact_sha256": binding["artifact_sha256"],
    }
    body = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
    d = hashlib.sha256(body).hexdigest()
    sig = hmac.new(
        SECRET.encode(), f"request-cover.1000.{d}".encode(), hashlib.sha256
    ).hexdigest()
    return body, {
        "X-Munshi-Event-Id": "request-cover",
        "X-Munshi-Timestamp": "1000",
        "X-Munshi-Content-SHA256": d,
        "X-Munshi-Signature": f"sha256={sig}",
    }


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET", SECRET)
    monkeypatch.setattr(
        bridge.application_plan_v2, "executable_plan", lambda _id: plan()
    )
    monkeypatch.setattr(
        bridge.cover_letter_artifact_v1, "artifact_bytes", lambda _id: COVER
    )


def test_exact_signed_cover_letter():
    b, h = req(bridge.PURPOSE_COVER_LETTER_BYTES, plan()["snapshot"]["cover_letter"])
    out, hs = bridge.cover_letter_response(b, h, now=1000)
    assert out == COVER
    assert hs["X-Munshi-Artifact-SHA256"] == COVER_SHA
    assert hs["X-Munshi-Submission-Authority"] == "false"
    assert hs["X-Munshi-Response-Purpose"] == bridge.PURPOSE_COVER_LETTER_BYTES


def test_cover_request_cannot_rebind_resume():
    b, h = req(bridge.PURPOSE_COVER_LETTER_BYTES, plan()["snapshot"]["resume"])
    with pytest.raises(PermissionError, match="immutable plan"):
        bridge.cover_letter_response(b, h, now=1000)
