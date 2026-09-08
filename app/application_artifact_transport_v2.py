"""Authenticated read-only Apply->Hunter execution bridge."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

from app import application_plan_v2, cover_letter_artifact_v1, native_resume_artifact_v5

REQUEST_VERSION = "munshi-application-execution-request-v1"
RESPONSE_VERSION = "munshi-application-execution-response-v1"
PURPOSE_PLAN_CURRENT = "PLAN_CURRENT"
PURPOSE_ARTIFACT_BYTES = "ARTIFACT_BYTES"
PURPOSE_COVER_LETTER_BYTES = "COVER_LETTER_BYTES"
MAX_SKEW_SECONDS = 120
REQUIRED = {
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


def _canonical(v: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(v), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def _secret() -> bytes:
    v = str(os.getenv("MUNSHI_APPLY_HANDOFF_HMAC_SECRET") or "")
    if len(v) < 16:
        raise RuntimeError("Apply handoff HMAC secret is not configured.")
    return v.encode()


def _header(h: Mapping[str, str], n: str) -> str:
    for k, v in h.items():
        if str(k).casefold() == n.casefold():
            return str(v)
    return ""


def _verify(
    body: bytes, headers: Mapping[str, str], *, purpose: str, now: int | None = None
) -> dict[str, Any]:
    if purpose not in {
        PURPOSE_PLAN_CURRENT,
        PURPOSE_ARTIFACT_BYTES,
        PURPOSE_COVER_LETTER_BYTES,
    }:
        raise ValueError("Unsupported execution bridge purpose.")
    d = hashlib.sha256(body).hexdigest()
    eid = _header(headers, "X-Munshi-Event-Id")
    tsx = _header(headers, "X-Munshi-Timestamp")
    if _header(headers, "X-Munshi-Content-SHA256") != d:
        raise PermissionError("Execution bridge content digest is invalid.")
    try:
        ts = int(tsx)
    except ValueError as e:
        raise PermissionError("Execution bridge timestamp is invalid.") from e
    if abs((int(time.time()) if now is None else int(now)) - ts) > MAX_SKEW_SECONDS:
        raise PermissionError("Execution bridge request is stale.")
    exp = hmac.new(_secret(), f"{eid}.{ts}.{d}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(_header(headers, "X-Munshi-Signature"), f"sha256={exp}"):
        raise PermissionError("Execution bridge signature is invalid.")
    try:
        p = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError("Execution bridge body is invalid JSON.") from e
    if not isinstance(p, dict) or set(p) != REQUIRED:
        raise ValueError("Execution bridge request shape is invalid.")
    if _canonical(p) != body:
        raise ValueError("Execution bridge request must use canonical JSON.")
    if p.get("version") != REQUEST_VERSION or p.get("purpose") != purpose:
        raise ValueError("Execution bridge request version/purpose is invalid.")
    if p.get("request_id") != eid or not eid:
        raise PermissionError("Execution bridge event identity mismatch.")
    return p


def _binding(snapshot: Mapping[str, Any], purpose: str) -> dict[str, Any]:
    if purpose == PURPOSE_COVER_LETTER_BYTES:
        v = snapshot.get("cover_letter")
        if not isinstance(v, Mapping):
            raise PermissionError(
                "Application Plan has no cover-letter artifact binding."
            )
        return dict(v)
    return dict(snapshot["resume"])


def _authorized(p: Mapping[str, Any], *, purpose: str):
    plan = application_plan_v2.executable_plan(str(p["plan_id"]))
    snap = dict(plan["snapshot"])
    a = _binding(snap, purpose)
    owner = dict(snap.get("owner_binding") or {})
    checks = (
        str(plan["tenant_id"]) == str(p["tenant_id"]),
        str(plan["user_id"]) == str(p["user_id"]),
        str(owner.get("tenant_id") or plan["tenant_id"]) == str(p["tenant_id"]),
        str(owner.get("user_id") or plan["user_id"]) == str(p["user_id"]),
        str(plan["application_id"]) == str(p["application_id"]),
        str(plan["plan_digest"]) == str(p["plan_digest"]),
        str(a["artifact_id"]) == str(p["artifact_id"]),
        str(a["artifact_reference"]) == str(p["artifact_reference"]),
        str(a["artifact_sha256"]) == str(p["artifact_sha256"]),
        snap.get("submission_authority") is False,
    )
    if not all(checks):
        raise PermissionError(
            "Execution bridge request conflicts with immutable plan binding."
        )
    return plan, snap, a


def _headers(
    *, request_id: str, purpose: str, body: bytes, plan_digest: str
) -> dict[str, str]:
    d = hashlib.sha256(body).hexdigest()
    sig = hmac.new(
        _secret(), f"{request_id}.{purpose}.{d}.{plan_digest}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-Munshi-Response-Event-Id": request_id,
        "X-Munshi-Response-Purpose": purpose,
        "X-Munshi-Response-SHA256": d,
        "X-Munshi-Plan-Digest": plan_digest,
        "X-Munshi-Response-Signature": f"sha256={sig}",
    }


def current_plan_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
):
    p = _verify(body, headers, purpose=PURPOSE_PLAN_CURRENT, now=now)
    plan, _, _ = _authorized(p, purpose=PURPOSE_PLAN_CURRENT)
    out = _canonical(
        {
            "version": RESPONSE_VERSION,
            "request_id": str(p["request_id"]),
            "purpose": PURPOSE_PLAN_CURRENT,
            "plan_id": str(plan["plan_id"]),
            "plan_digest": str(plan["plan_digest"]),
            "fresh": True,
            "submission_authority": False,
        }
    )
    return out, _headers(
        request_id=str(p["request_id"]),
        purpose=PURPOSE_PLAN_CURRENT,
        body=out,
        plan_digest=str(plan["plan_digest"]),
    )


def _artifact_response(
    body: bytes,
    headers: Mapping[str, str],
    *,
    purpose: str,
    reader: Callable[[str], bytes],
    now: int | None = None,
):
    p = _verify(body, headers, purpose=purpose, now=now)
    plan, _, a = _authorized(p, purpose=purpose)
    data = reader(str(a["artifact_id"]))
    d = hashlib.sha256(data).hexdigest()
    if d != str(a["artifact_sha256"]):
        raise RuntimeError(
            "Immutable Hunter artifact bytes do not match Application Plan digest."
        )
    hs = _headers(
        request_id=str(p["request_id"]),
        purpose=purpose,
        body=data,
        plan_digest=str(plan["plan_digest"]),
    )
    hs.update(
        {
            "X-Munshi-Artifact-Id": str(a["artifact_id"]),
            "X-Munshi-Artifact-SHA256": d,
            "X-Munshi-Submission-Authority": "false",
        }
    )
    return data, hs


def artifact_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
):
    return _artifact_response(
        body,
        headers,
        purpose=PURPOSE_ARTIFACT_BYTES,
        reader=native_resume_artifact_v5.artifact_bytes,
        now=now,
    )


def cover_letter_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
):
    return _artifact_response(
        body,
        headers,
        purpose=PURPOSE_COVER_LETTER_BYTES,
        reader=cover_letter_artifact_v1.artifact_bytes,
        now=now,
    )
