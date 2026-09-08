"""Authenticated read-only execution bridge from MUNSHI Apply to Hunter.

The bridge resolves only immutable resume bytes and current-plan status for an
already accepted Application Plan V2. It never grants submission authority and
has no browser, ATS, n8n, Gmail, credential, review, or submit action.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping
from typing import Any

from app import application_plan_v2, native_resume_artifact_v5

REQUEST_VERSION = "munshi-application-execution-request-v1"
RESPONSE_VERSION = "munshi-application-execution-response-v1"
PURPOSE_PLAN_CURRENT = "PLAN_CURRENT"
PURPOSE_ARTIFACT_BYTES = "ARTIFACT_BYTES"
MAX_SKEW_SECONDS = 120

_REQUIRED_FIELDS = {
    "version",
    "request_id",
    "purpose",
    "tenant_id",
    "user_id",
    "application_id",
    "plan_id",
    "plan_digest",
    "artifact_id",
    "artifact_reference",
    "artifact_sha256",
}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _secret() -> bytes:
    value = str(os.getenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET") or "")
    if len(value) < 16:
        raise RuntimeError("Apply handoff HMAC secret is not configured.")
    return value.encode("utf-8")


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if str(key).casefold() == name.casefold():
            return str(value)
    return ""


def _verify_request(body: bytes, headers: Mapping[str, str], *, purpose: str, now: int | None = None) -> dict[str, Any]:
    if purpose not in {PURPOSE_PLAN_CURRENT, PURPOSE_ARTIFACT_BYTES}:
        raise ValueError("Unsupported execution bridge purpose.")
    body_sha = hashlib.sha256(body).hexdigest()
    event_id = _header(headers, "X-Munshi-Event-Id")
    timestamp_text = _header(headers, "X-Munshi-Timestamp")
    content_sha = _header(headers, "X-Munshi-Content-SHA256")
    signature = _header(headers, "X-Munshi-Signature")
    if content_sha != body_sha:
        raise PermissionError("Execution bridge content digest is invalid.")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise PermissionError("Execution bridge timestamp is invalid.") from exc
    current = int(time.time()) if now is None else int(now)
    if abs(current - timestamp) > MAX_SKEW_SECONDS:
        raise PermissionError("Execution bridge request is stale.")
    expected = hmac.new(_secret(), f"{event_id}.{timestamp}.{body_sha}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, f"sha256={expected}"):
        raise PermissionError("Execution bridge signature is invalid.")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("Execution bridge body is invalid JSON.") from exc
    if not isinstance(payload, dict) or set(payload) != _REQUIRED_FIELDS:
        raise ValueError("Execution bridge request shape is invalid.")
    if _canonical(payload) != body:
        raise ValueError("Execution bridge request must use canonical JSON.")
    if payload.get("version") != REQUEST_VERSION:
        raise ValueError("Execution bridge request version is unsupported.")
    if payload.get("purpose") != purpose:
        raise ValueError("Execution bridge request purpose mismatch.")
    if payload.get("request_id") != event_id or not event_id:
        raise PermissionError("Execution bridge event identity mismatch.")
    return payload


def _authorized_plan(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = application_plan_v2.executable_plan(str(payload["plan_id"]))
    snapshot = dict(plan["snapshot"])
    resume = dict(snapshot["resume"])
    owner = dict(snapshot.get("owner_binding") or {})
    checks = (
        str(plan["tenant_id"]) == str(payload["tenant_id"]),
        str(plan["user_id"]) == str(payload["user_id"]),
        str(owner.get("tenant_id") or plan["tenant_id"]) == str(payload["tenant_id"]),
        str(owner.get("user_id") or plan["user_id"]) == str(payload["user_id"]),
        str(plan["application_id"]) == str(payload["application_id"]),
        str(plan["plan_digest"]) == str(payload["plan_digest"]),
        str(resume["artifact_id"]) == str(payload["artifact_id"]),
        str(resume["artifact_reference"]) == str(payload["artifact_reference"]),
        str(resume["artifact_sha256"]) == str(payload["artifact_sha256"]),
        snapshot.get("submission_authority") is False,
    )
    if not all(checks):
        raise PermissionError("Execution bridge request conflicts with immutable plan binding.")
    return plan, snapshot


def _response_headers(*, request_id: str, purpose: str, body: bytes, plan_digest: str) -> dict[str, str]:
    body_sha = hashlib.sha256(body).hexdigest()
    signature = hmac.new(_secret(), f"{request_id}.{purpose}.{body_sha}.{plan_digest}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Munshi-Response-Event-Id": request_id,
        "X-Munshi-Response-Purpose": purpose,
        "X-Munshi-Response-SHA256": body_sha,
        "X-Munshi-Plan-Digest": plan_digest,
        "X-Munshi-Response-Signature": f"sha256={signature}",
    }


def current_plan_response(body: bytes, headers: Mapping[str, str], *, now: int | None = None) -> tuple[bytes, dict[str, str]]:
    payload = _verify_request(body, headers, purpose=PURPOSE_PLAN_CURRENT, now=now)
    plan, _snapshot = _authorized_plan(payload)
    response = _canonical({
        "version": RESPONSE_VERSION,
        "request_id": str(payload["request_id"]),
        "purpose": PURPOSE_PLAN_CURRENT,
        "plan_id": str(plan["plan_id"]),
        "plan_digest": str(plan["plan_digest"]),
        "fresh": True,
        "submission_authority": False,
    })
    return response, _response_headers(request_id=str(payload["request_id"]), purpose=PURPOSE_PLAN_CURRENT, body=response, plan_digest=str(plan["plan_digest"]))


def artifact_response(body: bytes, headers: Mapping[str, str], *, now: int | None = None) -> tuple[bytes, dict[str, str]]:
    payload = _verify_request(body, headers, purpose=PURPOSE_ARTIFACT_BYTES, now=now)
    plan, snapshot = _authorized_plan(payload)
    resume = dict(snapshot["resume"])
    data = native_resume_artifact_v5.artifact_bytes(str(resume["artifact_id"]))
    digest = hashlib.sha256(data).hexdigest()
    if digest != str(resume["artifact_sha256"]):
        raise RuntimeError("Immutable Hunter artifact bytes do not match Application Plan digest.")
    response_headers = _response_headers(request_id=str(payload["request_id"]), purpose=PURPOSE_ARTIFACT_BYTES, body=data, plan_digest=str(plan["plan_digest"]))
    response_headers.update({
        "X-Munshi-Artifact-Id": str(resume["artifact_id"]),
        "X-Munshi-Artifact-SHA256": digest,
        "X-Munshi-Submission-Authority": "false",
    })
    return data, response_headers
