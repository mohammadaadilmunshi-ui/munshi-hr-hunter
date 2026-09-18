from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit

from app import application_plan_transport_v2 as transport

APPLY_BASE_URL_ENV = "MUNSHI_APPLY_BASE_URL"
HANDOFF_PATH = "/v1/application-plan-handoffs"


@dataclass(frozen=True)
class PlanDeliveryResult:
    handoff_id: str
    plan_id: str
    plan_digest: str
    state: str
    replayed: bool
    submission_authority: bool = False


def _base_url() -> str:
    raw = str(os.getenv(APPLY_BASE_URL_ENV) or "").strip()
    if not raw:
        raise RuntimeError("Apply base URL is not configured.")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Apply base URL is invalid.")
    return raw.rstrip("/")


def deliver_application_plan(
    *,
    plan_id: str,
    idempotency_key: str,
    timeout_seconds: float = 5.0,
) -> PlanDeliveryResult:
    if not transport.live_handoff_enabled():
        raise RuntimeError("Application Plan live handoff is disabled.")
    if timeout_seconds <= 0:
        raise ValueError("Apply handoff timeout must be positive.")

    signed = transport.create_signed_plan_transport(
        plan_id=plan_id,
        idempotency_key=idempotency_key,
    )
    try:
        envelope = json.loads(signed.body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Signed Application Plan transport body is invalid.") from exc

    handoff_id = str(envelope.get("handoff_id") or "")
    expected_plan_id = str(envelope.get("plan_id") or "")
    expected_plan_digest = str(envelope.get("plan_digest") or "")
    if not handoff_id or not expected_plan_id or len(expected_plan_digest) != 64:
        raise RuntimeError("Signed Application Plan transport identity is incomplete.")

    outbound = request.Request(
        _base_url() + HANDOFF_PATH,
        data=signed.body,
        headers=signed.headers,
        method="POST",
    )
    try:
        with request.urlopen(outbound, timeout=timeout_seconds) as response:  # noqa: S310
            status_code = int(getattr(response, "status", 0) or 0)
            response_body = response.read()
    except error.HTTPError as exc:
        raise RuntimeError(
            f"Apply rejected the Application Plan handoff with HTTP {exc.code}."
        ) from exc
    except error.URLError as exc:
        raise RuntimeError("Apply Application Plan handoff transport failed.") from exc

    if status_code not in {200, 202}:
        raise RuntimeError(
            f"Apply Application Plan handoff returned unexpected HTTP {status_code}."
        )

    try:
        acknowledgement: dict[str, Any] = json.loads(response_body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Apply Application Plan acknowledgement is invalid.") from exc

    if (
        acknowledgement.get("accepted") is not True
        or acknowledgement.get("state") != "PLAN_ACCEPTED"
        or str(acknowledgement.get("handoff_id") or "") != handoff_id
        or str(acknowledgement.get("plan_id") or "") != expected_plan_id
        or str(acknowledgement.get("plan_digest") or "") != expected_plan_digest
    ):
        raise RuntimeError("Apply Application Plan acknowledgement is not correlated.")

    recorded = transport.record_plan_accepted(
        handoff_id=handoff_id,
        plan_id=expected_plan_id,
        plan_digest=expected_plan_digest,
    )
    if (
        recorded.get("state") != "PLAN_ACCEPTED"
        or recorded.get("submission_authority") is not False
    ):
        raise RuntimeError("Hunter Application Plan acceptance ledger update failed.")

    return PlanDeliveryResult(
        handoff_id=handoff_id,
        plan_id=expected_plan_id,
        plan_digest=expected_plan_digest,
        state="PLAN_ACCEPTED",
        replayed=bool(acknowledgement.get("replayed")),
        submission_authority=False,
    )
