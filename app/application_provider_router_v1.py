"""Provider-neutral application ownership, routing and receipt states.

Hunter owns one durable route for an exact employer opening.  Selecting Tsenta
does not transfer the application ledger to Tsenta, and selecting Native does
not permit a second provider to race it.  Provider-reported completion and an
independently confirmed employer receipt are deliberately separate states.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app import application_preflight_package_v1 as preflight_v1
from app.database import get_connection
from app.linkedin_employer_link_resolver_v1 import external_url
from app.linkedin_verified_url_evidence_v5 import latest_verified_for_job
from app.tenant_foundation import current_owner, current_owner_readonly
from app.tenant_foundation import ensure_schema as ensure_tenant_schema

ROUTER_VERSION = "munshi-application-provider-router-v1"
EXECUTION_ENV = "MUNSHI_EXTERNAL_APPLICATION_EXECUTION_ENABLED"
PROVIDERS = frozenset({"NATIVE", "TSENTA"})
STATES = frozenset(
    {
        "PREPARATION",
        "FILLING",
        "PAUSED",
        "FAILED",
        "PROVIDER_SUBMITTED",
        "EMPLOYER_CONFIRMED",
    }
)
TERMINAL_RECEIPT_STATES = frozenset({"PROVIDER_SUBMITTED", "EMPLOYER_CONFIRMED"})
_SHA = re.compile(r"^[0-9a-f]{64}$")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_provider_routes_v1(
        route_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_identity_sha256 TEXT NOT NULL,
        employer_url TEXT NOT NULL,
        employer_url_sha256 TEXT NOT NULL,
        destination_evidence TEXT NOT NULL,
        provider TEXT NOT NULL CHECK(provider IN ('NATIVE','TSENTA')),
        state TEXT NOT NULL CHECK(state IN (
            'PREPARATION','FILLING','PAUSED','FAILED',
            'PROVIDER_SUBMITTED','EMPLOYER_CONFIRMED'
        )),
        preflight_package_id TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        candidate_profile_digest TEXT NOT NULL,
        resume_sha256 TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        provider_application_id_sha256 TEXT,
        provider_status TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
        max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
        next_attempt_at TEXT,
        last_error_code TEXT,
        receipt_method TEXT,
        receipt_evidence_digest TEXT,
        provider_submitted_at TEXT,
        employer_confirmed_at TEXT,
        locked_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(tenant_id,user_id,job_identity_sha256),
        UNIQUE(tenant_id,user_id,idempotency_key),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_provider_routes_v1_owner_state
       ON application_provider_routes_v1(tenant_id,user_id,state,updated_at DESC);""",
    """CREATE TABLE IF NOT EXISTS application_provider_route_events_v1(
        event_id TEXT PRIMARY KEY,
        route_id TEXT NOT NULL,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        from_state TEXT,
        to_state TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(route_id) REFERENCES application_provider_routes_v1(route_id)
          ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_provider_route_events_v1_route
       ON application_provider_route_events_v1(route_id,created_at);""",
)

_ALLOWED_TRANSITIONS = {
    "PREPARATION": frozenset({"FILLING", "PAUSED", "FAILED", "PROVIDER_SUBMITTED"}),
    "FILLING": frozenset({"FILLING", "PAUSED", "FAILED", "PROVIDER_SUBMITTED"}),
    "PAUSED": frozenset({"FILLING", "PAUSED", "FAILED", "PROVIDER_SUBMITTED"}),
    "FAILED": frozenset({"PREPARATION", "FAILED"}),
    "PROVIDER_SUBMITTED": frozenset({"PROVIDER_SUBMITTED", "EMPLOYER_CONFIRMED"}),
    "EMPLOYER_CONFIRMED": frozenset({"EMPLOYER_CONFIRMED"}),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _digest(value: Any, label: str) -> str:
    resolved = str(value or "").strip()
    if not _SHA.fullmatch(resolved):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return resolved


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _job_identity(job: Mapping[str, Any], employer_url: str) -> str:
    payload = {
        "company": str(job.get("company_name") or "").strip().casefold(),
        "title": str(job.get("title") or "").strip().casefold(),
        "requisition": str(job.get("ats_job_id") or "").strip().casefold(),
        "employer_url": employer_url,
    }
    if not payload["company"] or not payload["title"]:
        raise ValueError("Canonical job identity is incomplete.")
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _verified_destination(
    connection: sqlite3.Connection,
    job: Mapping[str, Any],
    requested_url: str,
) -> tuple[str, str]:
    destination = external_url(requested_url)
    if not destination:
        raise ValueError("A verified external employer application URL is required.")
    if "linkedin" in str(job.get("source") or "").casefold():
        evidence = latest_verified_for_job(connection, job)
        if evidence is None:
            raise PermissionError(
                "LinkedIn job has no current signed employer URL evidence."
            )
        expected = external_url(evidence.get("employer_apply_url"))
        if expected != destination:
            raise PermissionError(
                "Requested employer URL does not match signed job evidence."
            )
        return destination, "linkedin_signed_exact_job_v5"

    candidates = {
        value
        for value in (
            external_url(job.get("apply_url")),
            external_url(job.get("job_url")),
        )
        if value
    }
    if destination not in candidates:
        raise PermissionError(
            "Requested employer URL does not match the canonical job."
        )
    return destination, "canonical_job_destination"


def _assert_eligible(connection: sqlite3.Connection, job: Mapping[str, Any]) -> None:
    keys = set(job)
    if (
        "primary_decision" in keys
        and str(job.get("primary_decision") or "") != "ELIGIBLE"
    ):
        raise PermissionError(
            "Job is not currently eligible for application execution."
        )
    if str(job.get("status") or "found") != "found":
        raise PermissionError("Job is not in an executable discovered state.")
    if job.get("hard_rejection_reason"):
        raise PermissionError("Job has a hard eligibility rejection.")
    if bool(int(job.get("cpt_trapdoor") or 0)):
        raise PermissionError("Job is blocked by work-authorization eligibility.")
    if bool(int(job.get("already_applied") or 0)):
        raise FileExistsError("Job is already marked applied.")
    if _table_exists(connection, "application_lifecycle_records"):
        duplicate = connection.execute(
            """SELECT state FROM application_lifecycle_records
               WHERE job_id=? AND state IN (
                 'SUBMITTED','VERIFIED','AWAITING_RESPONSE','ASSESSMENT',
                 'INTERVIEW','OFFER','REJECTED','WITHDRAWN'
               ) LIMIT 1""",
            (int(job["id"]),),
        ).fetchone()
        if duplicate is not None:
            raise FileExistsError("Application lifecycle already records this job.")


def _preflight_binding(preflight_package_id: str) -> dict[str, Any]:
    package = preflight_v1.get_preflight_package(str(preflight_package_id))
    freshness = preflight_v1.preflight_package_freshness(str(preflight_package_id))
    if freshness.get("fresh") is not True:
        raise RuntimeError("Application preflight package is stale.")
    if str(package.get("status") or "") != "READY_TO_APPLY":
        raise ValueError("Provider routing requires a READY_TO_APPLY package.")
    snapshot = dict(package.get("snapshot") or {})
    truth = dict(snapshot.get("application_truth") or {})
    candidate = dict(truth.get("candidate_profile_binding") or {})
    return {
        "package": package,
        "package_digest": _digest(package.get("package_digest"), "Package digest"),
        "candidate_profile_digest": _digest(
            candidate.get("profile_digest"), "Candidate Truth digest"
        ),
        "resume_sha256": _digest(package.get("resume_sha256"), "Resume digest"),
    }


def _event(
    connection: sqlite3.Connection,
    *,
    route: Mapping[str, Any],
    event_type: str,
    from_state: str | None,
    to_state: str,
    evidence: Mapping[str, Any] | None = None,
) -> None:
    safe = {
        key: value
        for key, value in dict(evidence or {}).items()
        if key
        in {
            "provider_status",
            "error_code",
            "retry_scheduled",
            "receipt_method",
            "receipt_evidence_digest",
        }
    }
    created = _now()
    raw = f"{route['route_id']}:{event_type}:{from_state}:{to_state}:{created}:{_canonical(safe)}"
    event_id = "provider-event-" + hashlib.sha256(raw.encode()).hexdigest()[:32]
    connection.execute(
        """INSERT INTO application_provider_route_events_v1(
             event_id,route_id,tenant_id,user_id,event_type,from_state,to_state,
             evidence_json,created_at
           ) VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            route["route_id"],
            route["tenant_id"],
            route["user_id"],
            str(event_type),
            from_state,
            to_state,
            _canonical(safe),
            created,
        ),
    )


def select_provider(
    *,
    job_id: int,
    provider: str,
    verified_employer_url: str,
    preflight_package_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Acquire or replay the durable one-engine ownership lock."""
    selected = str(provider or "").strip().upper()
    if selected not in PROVIDERS:
        raise ValueError("Application provider must be NATIVE or TSENTA.")
    key = str(idempotency_key or "").strip()
    if not key or len(key) > 240:
        raise ValueError("Provider route idempotency key is required.")
    binding = _preflight_binding(preflight_package_id)
    package = binding["package"]
    if int(package["job_id"]) != int(job_id):
        raise ValueError("Preflight package belongs to another job.")

    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        owner = current_owner(connection)
        row = connection.execute(
            "SELECT * FROM jobs WHERE id=?", (int(job_id),)
        ).fetchone()
        if row is None:
            raise LookupError("Canonical job was not found.")
        job = dict(row)
        _assert_eligible(connection, job)
        destination, destination_evidence = _verified_destination(
            connection, job, verified_employer_url
        )
        identity = _job_identity(job, destination)
        prior_key = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE tenant_id=? AND user_id=? AND idempotency_key=?""",
            (owner.tenant_id, owner.user_id, key),
        ).fetchone()
        if prior_key is not None:
            result = dict(prior_key)
            if (
                str(result["job_identity_sha256"]) != identity
                or str(result["provider"]) != selected
            ):
                raise FileExistsError(
                    "Provider-route idempotency key already belongs to another opening."
                )
        prior = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE tenant_id=? AND user_id=? AND job_identity_sha256=?""",
            (owner.tenant_id, owner.user_id, identity),
        ).fetchone()
        if prior is not None or prior_key is not None:
            prior = prior or prior_key
            result = dict(prior)
            if str(result["provider"]) != selected:
                raise FileExistsError(
                    "Exact opening is already owned by another execution provider."
                )
            expected = (
                str(result["preflight_package_id"]) == str(preflight_package_id)
                and str(result["package_digest"]) == binding["package_digest"]
                and str(result["candidate_profile_digest"])
                == binding["candidate_profile_digest"]
                and str(result["resume_sha256"]) == binding["resume_sha256"]
                and str(result["employer_url"]) == destination
            )
            if not expected:
                raise RuntimeError(
                    "Existing provider lock is bound to different application evidence."
                )
            result["replayed"] = True
            result["execution_enabled"] = False
            return result

        raw = (
            f"{owner.tenant_id}:{owner.user_id}:{identity}:{selected}:"
            f"{binding['package_digest']}:{ROUTER_VERSION}"
        )
        route_id = "provider-route-" + hashlib.sha256(raw.encode()).hexdigest()[:32]
        locked_at = _now()
        route = {
            "route_id": route_id,
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "application_id": str(package["application_id"]),
            "job_id": int(job_id),
            "job_identity_sha256": identity,
            "employer_url": destination,
            "employer_url_sha256": hashlib.sha256(destination.encode()).hexdigest(),
            "destination_evidence": destination_evidence,
            "provider": selected,
            "state": "PREPARATION",
            "preflight_package_id": str(preflight_package_id),
            "package_digest": binding["package_digest"],
            "candidate_profile_digest": binding["candidate_profile_digest"],
            "resume_sha256": binding["resume_sha256"],
            "idempotency_key": key,
            "locked_at": locked_at,
            "updated_at": locked_at,
        }
        connection.execute(
            """INSERT INTO application_provider_routes_v1(
                 route_id,tenant_id,user_id,application_id,job_id,
                 job_identity_sha256,employer_url,employer_url_sha256,
                 destination_evidence,provider,state,preflight_package_id,
                 package_digest,candidate_profile_digest,resume_sha256,
                 idempotency_key,locked_at,updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            tuple(route.values()),
        )
        _event(
            connection,
            route=route,
            event_type="PROVIDER_SELECTED",
            from_state=None,
            to_state="PREPARATION",
        )
        connection.commit()
        return {**route, "replayed": False, "execution_enabled": False}
    finally:
        connection.close()


def get_route(route_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE route_id=? AND tenant_id=? AND user_id=?""",
            (str(route_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Provider route was not found for the current owner.")
        return dict(row)
    finally:
        connection.close()


def get_route_by_application_id(application_id: str) -> dict[str, Any] | None:
    """Tenant-scoped read-only lookup for the existing extension application ID.

    Unlike get_route, this does not install schemas or write audit records.
    It cannot identify jobs by company/title or create a new provider route.
    """
    raw = str(application_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", raw):
        raise ValueError("A bounded application identity is required.")
    connection = get_connection()
    try:
        installed = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='application_provider_routes_v1'"
        ).fetchone()
        if installed is None:
            return None
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT route_id, application_id, job_id, provider, state,
                      attempt_count, updated_at, receipt_evidence_digest
                 FROM application_provider_routes_v1
                WHERE tenant_id=? AND user_id=? AND application_id=?
                ORDER BY datetime(updated_at) DESC, rowid DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, raw),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _provider_application_digest(value: str | None) -> str | None:
    resolved = str(value or "").strip()
    return hashlib.sha256(resolved.encode()).hexdigest() if resolved else None


def _safe_provider_status(value: str) -> str:
    status = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", status):
        raise ValueError("Provider status must be a bounded machine code.")
    return status


def _safe_error_code(value: str | None) -> str | None:
    if not value:
        return None
    code = str(value).strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,71}", code):
        return code
    return "PROVIDER_FAILURE_UNCLASSIFIED"


def record_provider_status(
    *,
    route_id: str,
    provider_status: str,
    provider_application_id: str | None = None,
    error_code: str | None = None,
) -> dict[str, Any]:
    """Project a provider state without claiming employer confirmation."""
    status = _safe_provider_status(provider_status)
    safe_error_code = _safe_error_code(error_code)
    if status in {"COMPLETED", "SUBMITTED", "SENT"}:
        target = "PROVIDER_SUBMITTED"
    elif status in {"PENDING_SUBMIT_REVIEW", "WAITING_INPUT", "PAUSED"}:
        target = "PAUSED"
    elif status in {"FAILED", "FAILED_SAFELY", "SKIPPED", "CANCELLED", "BLOCKED"}:
        target = "FAILED"
    else:
        target = "FILLING"

    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE route_id=? AND tenant_id=? AND user_id=?""",
            (str(route_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Provider route was not found for the current owner.")
        route = dict(row)
        current = str(route["state"])
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise RuntimeError(
                f"Unsafe provider state transition: {current} -> {target}"
            )
        attempts = int(route["attempt_count"] or 0)
        retry_at = route.get("next_attempt_at")
        if target in {"FILLING", "PROVIDER_SUBMITTED"} and current == "PREPARATION":
            attempts += 1
        if attempts > int(route["max_attempts"]):
            raise RuntimeError("Provider route exhausted its bounded attempt budget.")
        if target == "FAILED" and attempts < int(route["max_attempts"]):
            retry_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat(
                timespec="seconds"
            )
        elif target != "FAILED":
            retry_at = None
        submitted_at = (
            _now()
            if target == "PROVIDER_SUBMITTED"
            else route.get("provider_submitted_at")
        )
        provider_application_digest = _provider_application_digest(
            provider_application_id
        ) or route.get("provider_application_id_sha256")
        updated = _now()
        connection.execute(
            """UPDATE application_provider_routes_v1
                  SET state=?,provider_status=?,provider_application_id_sha256=?,
                      attempt_count=?,next_attempt_at=?,last_error_code=?,
                      provider_submitted_at=?,updated_at=?
                WHERE route_id=?""",
            (
                target,
                status,
                provider_application_digest,
                attempts,
                retry_at,
                safe_error_code,
                submitted_at,
                updated,
                str(route_id),
            ),
        )
        _event(
            connection,
            route=route,
            event_type="PROVIDER_STATUS_OBSERVED",
            from_state=current,
            to_state=target,
            evidence={
                "provider_status": status,
                "error_code": safe_error_code,
                "retry_scheduled": bool(retry_at),
            },
        )
        connection.commit()
        return get_route(str(route_id))
    finally:
        connection.close()


def claim_due_retry(route_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Atomically reopen one failed route when its bounded backoff is due."""
    current_time = now or datetime.now(UTC)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE route_id=? AND tenant_id=? AND user_id=?""",
            (str(route_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Provider route was not found for the current owner.")
        route = dict(row)
        if str(route["state"]) != "FAILED":
            raise RuntimeError("Only a failed provider route can be retried.")
        if int(route["attempt_count"] or 0) >= int(route["max_attempts"]):
            raise RuntimeError("Provider route exhausted its bounded attempt budget.")
        retry_at = str(route.get("next_attempt_at") or "")
        if not retry_at:
            raise RuntimeError("Provider route has no scheduled retry.")
        try:
            retry_time = datetime.fromisoformat(retry_at)
        except ValueError as error:
            raise RuntimeError("Provider retry schedule is invalid.") from error
        if retry_time > current_time:
            raise RuntimeError("Provider retry backoff has not elapsed.")
        connection.execute(
            """UPDATE application_provider_routes_v1
                  SET state='PREPARATION',next_attempt_at=NULL,updated_at=?
                WHERE route_id=?""",
            (_now(), str(route_id)),
        )
        _event(
            connection,
            route=route,
            event_type="PROVIDER_RETRY_CLAIMED",
            from_state="FAILED",
            to_state="PREPARATION",
        )
        connection.commit()
        return get_route(str(route_id))
    finally:
        connection.close()


def confirm_employer_receipt(
    *,
    route_id: str,
    verification_method: str,
    evidence_digest: str,
) -> dict[str, Any]:
    """Promote provider-submitted only with independent receipt evidence."""
    method = str(verification_method or "").strip().upper()
    if method not in {
        "EMPLOYER_CONFIRMATION_PAGE",
        "CORRELATED_EMPLOYER_EMAIL",
        "EMPLOYER_APPLICATION_PORTAL",
    }:
        raise ValueError(
            "Independent employer receipt verification method is required."
        )
    digest = _digest(evidence_digest, "Receipt evidence digest")
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM application_provider_routes_v1
               WHERE route_id=? AND tenant_id=? AND user_id=?""",
            (str(route_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Provider route was not found for the current owner.")
        route = dict(row)
        current = str(route["state"])
        if "EMPLOYER_CONFIRMED" not in _ALLOWED_TRANSITIONS[current]:
            raise RuntimeError("Employer receipt cannot precede provider submission.")
        confirmed_at = route.get("employer_confirmed_at") or _now()
        connection.execute(
            """UPDATE application_provider_routes_v1
                  SET state='EMPLOYER_CONFIRMED',receipt_method=?,
                      receipt_evidence_digest=?,employer_confirmed_at=?,updated_at=?
                WHERE route_id=?""",
            (method, digest, confirmed_at, _now(), str(route_id)),
        )
        _event(
            connection,
            route=route,
            event_type="EMPLOYER_RECEIPT_CONFIRMED",
            from_state=current,
            to_state="EMPLOYER_CONFIRMED",
            evidence={
                "receipt_method": method,
                "receipt_evidence_digest": digest,
            },
        )
        connection.commit()
        return get_route(str(route_id))
    finally:
        connection.close()


def dispatch_envelope(route_id: str) -> dict[str, Any]:
    """Return a value-free, digest-bound request; never performs execution."""
    route = get_route(route_id)
    enabled = str(os.getenv(EXECUTION_ENV) or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }
    return {
        "version": ROUTER_VERSION,
        "route_id": str(route["route_id"]),
        "application_id": str(route["application_id"]),
        "job_id": int(route["job_id"]),
        "provider": str(route["provider"]),
        "state": str(route["state"]),
        "verified_employer_url": str(route["employer_url"]),
        "employer_url_sha256": str(route["employer_url_sha256"]),
        "preflight_package_id": str(route["preflight_package_id"]),
        "package_digest": str(route["package_digest"]),
        "candidate_profile_digest": str(route["candidate_profile_digest"]),
        "resume_sha256": str(route["resume_sha256"]),
        "execution_enabled": enabled,
        "submit_authorized": False,
        "contains_candidate_personal_data": False,
    }
