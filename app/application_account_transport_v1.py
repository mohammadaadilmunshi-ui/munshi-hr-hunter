"""Plan-bound account/mail execution transport owned by Hunter."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app import application_plan_v2
from app.application_artifact_transport_v2 import (
    REQUEST_VERSION,
    RESPONSE_VERSION,
    _canonical,
    _decode,
    _header,
    _headers,
    _secret,
    _verify_signature,
)
from app.ats_credentials import resolve_secret_for_execution
from app.ats_managed_identity import managed_secret_ref, prepare_managed_account
from app.ats_verification_orchestrator import begin_verification
from app.tenant_foundation import owner_context
from app.mailbox_verification_service import (
    cancel_request,
    claim_artifact,
    consume_artifact,
    runtime_health,
)

PURPOSE_ACCOUNT_PREPARE = "ATS_ACCOUNT_PREPARE"
PURPOSE_ACCOUNT_CREDENTIAL = "ATS_ACCOUNT_CREDENTIAL"
PURPOSE_MAILBOX_HEALTH = "MAILBOX_HEALTH"
PURPOSE_MAILBOX_BEGIN = "MAILBOX_VERIFICATION_BEGIN"
PURPOSE_MAILBOX_CLAIM = "MAILBOX_VERIFICATION_CLAIM"
PURPOSE_MAILBOX_CONSUME = "MAILBOX_VERIFICATION_CONSUME"
PURPOSE_MAILBOX_CANCEL = "MAILBOX_VERIFICATION_CANCEL"

PURPOSES = frozenset(
    {
        PURPOSE_ACCOUNT_PREPARE,
        PURPOSE_ACCOUNT_CREDENTIAL,
        PURPOSE_MAILBOX_HEALTH,
        PURPOSE_MAILBOX_BEGIN,
        PURPOSE_MAILBOX_CLAIM,
        PURPOSE_MAILBOX_CONSUME,
        PURPOSE_MAILBOX_CANCEL,
    }
)
_REQUIRED = {
    "version",
    "request_id",
    "purpose",
    "tenant_id",
    "user_id",
    "application_id",
    "plan_id",
    "plan_digest",
    "payload",
}


class ApplicationAccountTransportError(RuntimeError):
    pass


def _verify_request(
    body: bytes,
    headers: Mapping[str, str],
    *,
    purpose: str,
    now: int | None = None,
) -> dict[str, Any]:
    if purpose not in PURPOSES:
        raise ValueError("Unsupported account execution purpose.")
    event_id = _verify_signature(body, headers, now=now)
    payload = _decode(body)
    if set(payload) != _REQUIRED:
        raise ValueError("Account execution request shape is invalid.")
    if payload.get("version") != REQUEST_VERSION or payload.get("purpose") != purpose:
        raise ValueError("Account execution request version/purpose is invalid.")
    if payload.get("request_id") != event_id or not event_id:
        raise PermissionError("Account execution event identity mismatch.")
    if not isinstance(payload.get("payload"), dict):
        raise ValueError("Account execution payload must be an object.")
    return payload


def _authorized_plan(request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = application_plan_v2.executable_plan(str(request["plan_id"]))
    snapshot = dict(plan["snapshot"])
    owner = dict(snapshot.get("owner_binding") or {})
    checks = (
        str(plan["tenant_id"]) == str(request["tenant_id"]),
        str(plan["user_id"]) == str(request["user_id"]),
        str(owner.get("tenant_id") or plan["tenant_id"]) == str(request["tenant_id"]),
        str(owner.get("user_id") or plan["user_id"]) == str(request["user_id"]),
        str(plan["application_id"]) == str(request["application_id"]),
        str(plan["plan_digest"]) == str(request["plan_digest"]),
        snapshot.get("submission_authority") is False,
    )
    if not all(checks):
        raise PermissionError("Account execution request conflicts with immutable plan binding.")
    return plan, snapshot


def _provider(snapshot: Mapping[str, Any]) -> str:
    policy = snapshot.get("provider_policy")
    if not isinstance(policy, Mapping):
        raise PermissionError("Application Plan provider binding is unavailable.")
    value = str(policy.get("provider") or "").strip().upper()
    if not value:
        raise PermissionError("Application Plan provider binding is unavailable.")
    return value


def _required_text(payload: Mapping[str, Any], key: str, *, maximum: int = 512) -> str:
    value = str(payload.get(key) or "").strip()
    if not value or len(value) > maximum:
        raise ValueError(f"{key} is required.")
    return value


def _key(*, purpose: str, request_id: str, plan_digest: str) -> bytes:
    return hmac.new(
        _secret(),
        f"account-mail:{purpose}:{request_id}:{plan_digest}".encode(),
        hashlib.sha256,
    ).digest()


def _aad(*, purpose: str, request_id: str, plan_digest: str) -> bytes:
    return f"{request_id}.{purpose}.{plan_digest}".encode()


def _seal(
    secret_payload: Mapping[str, Any],
    *,
    purpose: str,
    request_id: str,
    plan_digest: str,
) -> str:
    nonce = __import__("os").urandom(12)
    raw = _canonical(dict(secret_payload))
    encrypted = AESGCM(
        _key(purpose=purpose, request_id=request_id, plan_digest=plan_digest)
    ).encrypt(
        nonce,
        raw,
        _aad(purpose=purpose, request_id=request_id, plan_digest=plan_digest),
    )
    return base64.urlsafe_b64encode(nonce + encrypted).decode()


def _open(
    envelope: object,
    *,
    purpose: str,
    request_id: str,
    plan_digest: str,
) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(str(envelope or "").encode())
        if len(raw) < 29:
            raise ValueError
        decoded = AESGCM(
            _key(purpose=purpose, request_id=request_id, plan_digest=plan_digest)
        ).decrypt(
            raw[:12],
            raw[12:],
            _aad(purpose=purpose, request_id=request_id, plan_digest=plan_digest),
        )
        value = json.loads(decoded)
    except Exception as error:
        raise PermissionError("Account execution sealed payload is invalid.") from error
    if not isinstance(value, dict):
        raise PermissionError("Account execution sealed payload is invalid.")
    return value


def _response(
    request: Mapping[str, Any],
    *,
    purpose: str,
    result: Mapping[str, Any],
) -> tuple[bytes, dict[str, str]]:
    body = _canonical(
        {
            "version": RESPONSE_VERSION,
            "request_id": str(request["request_id"]),
            "purpose": purpose,
            "plan_id": str(request["plan_id"]),
            "plan_digest": str(request["plan_digest"]),
            "result": dict(result),
            "submission_authority": False,
        }
    )
    headers = _headers(
        request_id=str(request["request_id"]),
        purpose=purpose,
        body=body,
        plan_digest=str(request["plan_digest"]),
    )
    headers["X-Munshi-Submission-Authority"] = "false"
    return body, headers


def mailbox_health_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_MAILBOX_HEALTH, now=now)
    _authorized_plan(request)
    return _response(request, purpose=PURPOSE_MAILBOX_HEALTH, result=runtime_health())


def account_prepare_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_ACCOUNT_PREPARE, now=now)
    _plan, snapshot = _authorized_plan(request)
    payload = dict(request["payload"])
    provider = _required_text(payload, "provider", maximum=80).upper()
    if provider != _provider(snapshot):
        raise PermissionError("Managed account provider conflicts with Application Plan.")
    with owner_context(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
    ):
        account = prepare_managed_account(
            provider=provider,
            account_scope=_required_text(payload, "account_scope", maximum=240),
            label=_required_text(payload, "label", maximum=120),
            consent_version=str(payload.get("consent_version") or "managed-ats-v1")[:80],
        )
    return _response(request, purpose=PURPOSE_ACCOUNT_PREPARE, result=account)


def account_credential_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_ACCOUNT_CREDENTIAL, now=now)
    _plan, snapshot = _authorized_plan(request)
    payload = dict(request["payload"])
    account_id = _required_text(payload, "account_id", maximum=128)
    secret_ref = _required_text(payload, "secret_ref", maximum=256)
    if not hmac.compare_digest(secret_ref, managed_secret_ref(account_id)):
        raise PermissionError("Managed account credential reference is invalid.")
    with owner_context(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
    ):
        password = resolve_secret_for_execution(
            account_id=account_id,
            tenant_id=str(request["tenant_id"]),
            user_id=str(request["user_id"]),
            secret_kind="password",
            purpose="application-execution",
        )
    try:
        sealed = _seal(
            {"password": password, "account_id": account_id, "secret_ref": secret_ref},
            purpose=PURPOSE_ACCOUNT_CREDENTIAL,
            request_id=str(request["request_id"]),
            plan_digest=str(request["plan_digest"]),
        )
    finally:
        password = ""
    return _response(
        request,
        purpose=PURPOSE_ACCOUNT_CREDENTIAL,
        result={"account_id": account_id, "secret_ref": secret_ref, "sealed": sealed},
    )


def mailbox_begin_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_MAILBOX_BEGIN, now=now)
    _plan, snapshot = _authorized_plan(request)
    health = runtime_health()
    if health["ready"] is not True:
        raise ApplicationAccountTransportError("Mandatory mailbox runtime is unavailable.")
    payload = dict(request["payload"])
    provider = _required_text(payload, "provider", maximum=80).upper()
    if provider != _provider(snapshot):
        raise PermissionError("Mailbox provider conflicts with Application Plan.")
    application_key = _required_text(payload, "application_key", maximum=256)
    if application_key != str(request["application_id"]):
        raise PermissionError("Mailbox application binding is invalid.")
    with owner_context(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
    ):
        result = begin_verification(
            account_id=_required_text(payload, "account_id", maximum=128),
            provider=provider,
            expected_link_hosts=list(payload.get("expected_link_hosts") or []),
            expected_sender_domains=list(payload.get("expected_sender_domains") or []),
            application_key=application_key,
            ttl_minutes=int(payload.get("ttl_minutes") or 30),
        )
    if result.get("mail_relay_ready") is not True:
        raise ApplicationAccountTransportError("Mandatory mailbox runtime is unavailable.")
    return _response(request, purpose=PURPOSE_MAILBOX_BEGIN, result=result)


def mailbox_claim_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_MAILBOX_CLAIM, now=now)
    _authorized_plan(request)
    payload = dict(request["payload"])
    claimed = claim_artifact(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
        request_id=_required_text(payload, "request_id", maximum=128),
        application_key=_required_text(payload, "application_key", maximum=256),
        account_id=_required_text(payload, "account_id", maximum=128),
        expected_kind=_required_text(payload, "expected_kind", maximum=80),
    )
    secret = {
        "artifact": claimed.pop("artifact"),
        "lease_token": claimed.pop("lease_token"),
    }
    claimed["sealed"] = _seal(
        secret,
        purpose=PURPOSE_MAILBOX_CLAIM,
        request_id=str(request["request_id"]),
        plan_digest=str(request["plan_digest"]),
    )
    return _response(request, purpose=PURPOSE_MAILBOX_CLAIM, result=claimed)


def mailbox_consume_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_MAILBOX_CONSUME, now=now)
    _authorized_plan(request)
    payload = dict(request["payload"])
    opened = _open(
        payload.get("sealed"),
        purpose=PURPOSE_MAILBOX_CONSUME,
        request_id=str(request["request_id"]),
        plan_digest=str(request["plan_digest"]),
    )
    result = consume_artifact(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
        artifact_id=_required_text(payload, "artifact_id", maximum=128),
        request_id=_required_text(payload, "request_id", maximum=128),
        application_key=_required_text(payload, "application_key", maximum=256),
        account_id=_required_text(payload, "account_id", maximum=128),
        lease_token=_required_text(opened, "lease_token", maximum=512),
    )
    return _response(request, purpose=PURPOSE_MAILBOX_CONSUME, result=result)


def mailbox_cancel_response(
    body: bytes, headers: Mapping[str, str], *, now: int | None = None
) -> tuple[bytes, dict[str, str]]:
    request = _verify_request(body, headers, purpose=PURPOSE_MAILBOX_CANCEL, now=now)
    _authorized_plan(request)
    payload = dict(request["payload"])
    application_key = _required_text(payload, "application_key", maximum=256)
    if application_key != str(request["application_id"]):
        raise PermissionError("Mailbox cancellation application binding is invalid.")
    result = cancel_request(
        tenant_id=str(request["tenant_id"]),
        user_id=str(request["user_id"]),
        request_id=_required_text(payload, "request_id", maximum=128),
        application_key=application_key,
        account_id=_required_text(payload, "account_id", maximum=128),
        reason_code=str(payload.get("reason_code") or "VERIFICATION_NOT_REQUIRED"),
    )
    return _response(request, purpose=PURPOSE_MAILBOX_CANCEL, result=result)
