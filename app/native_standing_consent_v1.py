"""Explicit owner consent ledger for future Native applications, default off.

Uses the existing candidate_digital_twin_preferences table (no schema changes).
Reservations only admit one exact package; they do not authorize browser submit.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

KEY = "native_standing_consent_v1"
PHRASE = "ENABLE BOUNDED NATIVE AUTO APPLY"
PREFIX = "native-approval-slot-v1-"


def _utc() -> datetime:
    return datetime.now(UTC)


def _parse(value: object) -> datetime | None:
    try:
        date = datetime.fromisoformat(str(value))
        return date.astimezone(UTC) if date.tzinfo else date.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _schema(connection) -> None:
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' "
        "AND name='candidate_digital_twin_preferences'"
    ).fetchone() is None:
        raise RuntimeError("Standing consent owner table is not installed.")


def _owner(connection) -> tuple[str, str]:
    owner = current_owner_readonly(connection)
    return owner.tenant_id, owner.user_id


def _get(connection) -> dict:
    row = connection.execute(
        "SELECT value_json,user_confirmed FROM candidate_digital_twin_preferences "
        "WHERE tenant_id=? AND user_id=? AND preference_key=?",
        (*_owner(connection), KEY),
    ).fetchone()
    if not row or int(row["user_confirmed"]) != 1:
        return {}
    try:
        value = json.loads(str(row["value_json"]))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _save(connection, key: str, data: dict) -> None:
    connection.execute(
        "INSERT INTO candidate_digital_twin_preferences("
        "preference_id,tenant_id,user_id,preference_key,value_json,"
        "provenance,confidence,user_confirmed) VALUES(?,?,?,?,?,?,1,1) "
        "ON CONFLICT(tenant_id,user_id,preference_key) DO UPDATE SET "
        "value_json=excluded.value_json,user_confirmed=1,"
        "updated_at=CURRENT_TIMESTAMP",
        (
            str(uuid4()), *_owner(connection), key,
            json.dumps(data,sort_keys=True,separators=(",",":")),
            "OWNER_EXPLICIT_NATIVE_CONSENT",
        ),
    )


def status(*, now: datetime | None = None) -> dict:
    """Read-only; any missing/stale/unknown preference fails closed."""
    con = get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        _schema(con)
        value = _get(con)
        current = now or _utc()
        expiry = _parse(value.get("expires_at"))
        live = (
            value.get("enabled") is True
            and value.get("version") == 1
            and bool(value.get("policy_id"))
            and bool(value.get("companies"))
            and expiry is not None and expiry > current
            and value.get("provider") == "NATIVE"
        )
        return {**value, "enabled": bool(live)} if live else {"enabled": False}
    except (RuntimeError, ValueError, TypeError, sqlite3.Error):
        return {"enabled": False}
    finally:
        con.close()


def grant(*, company_names: list[str], score: int, daily_cap: int,
          hours: int, confirmation: str) -> dict:
    """One explicit owner action. Never invoked by the preparation worker."""
    if confirmation != PHRASE:
        raise PermissionError("Exact standing consent confirmation required.")
    if type(score) is not int or not 95 <= score <= 100:
        raise ValueError("Minimum match score must be 95–100.")
    if type(daily_cap) is not int or not 1 <= daily_cap <= 3:
        raise ValueError("Daily limit must be 1–3 applications.")
    if type(hours) is not int or not 1 <= hours <= 168:
        raise ValueError("Consent expires within 7 days.")
    if not isinstance(company_names,list) or not 1 <= len(company_names) <= 8:
        raise ValueError("Choose 1–8 exact employer names.")
    companies = [str(name).strip().casefold() for name in company_names]
    if len(set(companies)) != len(companies) or any(
        not re.fullmatch(r"[\w .,&()/'-]{3,120}", company)
        or company in {"any","all","*","all employers"} for company in companies
    ):
        raise ValueError("Employer names must be exact, bounded, and unique.")
    now = _utc()
    value = {
        "version":1,"provider":"NATIVE","enabled":True,
        "policy_id":"consent-"+uuid4().hex,"companies":companies,
        "min_score":score,"daily_cap":daily_cap,
        "granted_at":now.isoformat(),
        "expires_at":(now+timedelta(hours=hours)).isoformat(),
    }
    con = get_connection()
    try:
        _schema(con)
        con.execute("BEGIN IMMEDIATE")
        _save(con, KEY, value)
        _save(con, "native-consent-audit-"+uuid4().hex,
              {"event":"GRANT","policy_id":value["policy_id"],"at":now.isoformat()})
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return value


def revoke() -> None:
    """Disable future autonomous admissions; does not replay old mandates."""
    con = get_connection()
    try:
        _schema(con)
        con.execute("BEGIN IMMEDIATE")
        value = _get(con)
        value["enabled"] = False
        value["revoked_at"] = _utc().isoformat()
        _save(con, KEY, value)
        _save(con, "native-consent-audit-"+uuid4().hex,
              {"event":"REVOKE","policy_id":value.get("policy_id"),"at":_utc().isoformat()})
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def reserve_fresh_package(*, job: dict, review_digest: str,
                          operation_created_at: str) -> dict:
    """Atomically consume a policy slot. No mandate or submit is created here.

    One slot per immutable review digest and job, only for operations CREATED
    after user consent. Historical failures cannot become newly authorized.
    """
    if not re.fullmatch(r"[0-9a-f]{64}",str(review_digest)):
        return {"reserved":False,"reason":"unverified_review_digest"}
    con = get_connection()
    try:
        _schema(con)
        con.execute("BEGIN IMMEDIATE")
        policy = _get(con)
        now = _utc()
        expires = _parse(policy.get("expires_at"))
        granted = _parse(policy.get("granted_at"))
        prepared_at = _parse(operation_created_at)
        allowed = (
            policy.get("version") == 1
            and policy.get("enabled") is True
            and policy.get("provider") == "NATIVE"
            and expires is not None and now < expires
            and granted is not None and prepared_at is not None
            and prepared_at >= granted
            and str(job.get("company_name") or "").strip().casefold()
                in policy.get("companies",[])
            and float(job.get("hunter_score") or 0)
                >= int(policy.get("min_score") or 101)
            and str(job.get("primary_decision") or "") == "ELIGIBLE"
            and str(job.get("status") or "") == "found"
            and not job.get("hard_rejection_reason")
            and not int(job.get("already_applied") or 0)
            and not int(job.get("cpt_trapdoor") or 0)
        )
        if not allowed:
            con.commit()
            return {"reserved":False,"reason":"consent_scope_or_freshness"}
        today=now.date().isoformat()
        slot=PREFIX+today+"-"+hashlib.sha256(
            (str(job["id"])+":"+review_digest).encode()
        ).hexdigest()[:40]
        owner=_owner(con)
        duplicate=con.execute(
            "SELECT 1 FROM candidate_digital_twin_preferences WHERE "
            "tenant_id=? AND user_id=? AND preference_key=?",
            (*owner,slot),
        ).fetchone()
        if duplicate:
            con.commit()
            return {"reserved":False,"reason":"already_reserved"}
        used=con.execute(
            "SELECT COUNT(*) FROM candidate_digital_twin_preferences WHERE "
            "tenant_id=? AND user_id=? AND preference_key LIKE ?",
            (*owner,PREFIX+today+"-%"),
        ).fetchone()[0]
        if used >= int(policy.get("daily_cap") or 0):
            con.commit()
            return {"reserved":False,"reason":"daily_limit_reached"}
        _save(con,slot,{"policy_id":policy["policy_id"],
                        "job_id":int(job["id"]),"digest":review_digest,
                        "at":now.isoformat()})
        con.commit()
        return {"reserved":True,"policy_id":policy["policy_id"],"slot":slot}
    except (ValueError,TypeError,KeyError):
        con.rollback()
        return {"reserved":False,"reason":"invalid_job_or_policy"}
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def validate_reserved_slot(*, slot: str, policy_id: str,
                           job_id: int, review_digest: str) -> bool:
    """Fail closed if reserved proof, current owner, or consent has changed."""
    if not str(slot).startswith(PREFIX):
        return False
    con=get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        _schema(con)
        policy=_get(con)
        row=con.execute(
            "SELECT value_json,user_confirmed FROM candidate_digital_twin_preferences "
            "WHERE tenant_id=? AND user_id=? AND preference_key=?",
            (*_owner(con),slot),
        ).fetchone()
        if not row or int(row["user_confirmed"]) != 1:
            return False
        record=json.loads(str(row["value_json"]))
        until=_parse(policy.get("expires_at"))
        return bool(
            policy.get("enabled") is True
            and policy.get("policy_id") == policy_id
            and until is not None and until > _utc()
            and record.get("policy_id") == policy_id
            and int(record.get("job_id") or 0) == int(job_id)
            and record.get("digest") == review_digest
        )
    except (ValueError, TypeError, RuntimeError, KeyError):
        return False
    finally:
        con.close()


def maybe_authorize_fresh_operation(operation_id: str) -> dict:
    """Admit only a fresh, fully-preflighted, exact consented Native job.

    This may record a one-job mandate. It never clicks submit or replays an
    employer account session, and runs only during NEW worker preparation.
    """
    policy=status()
    if not policy.get("enabled"):
        return {"authorized":False,"reason":"standing_consent_disabled"}
    from app import prepare_orchestrator_v1 as orchestrator
    from app import product_application_flow_v2 as flow

    operation=orchestrator.prepare_operation(str(operation_id))
    if str(operation.get("engine") or "") != "NATIVE":
        return {"authorized":False,"reason":"wrong_engine"}
    job_id=int(operation["job_id"])
    snapshot=flow.flow_for_job(job_id)
    if (
        snapshot.get("state") != "READY_FOR_APPROVAL"
        or str((snapshot.get("prepare") or {}).get("operation_id") or "")
            != str(operation_id)
    ):
        return {"authorized":False,"reason":"not_exact_ready_preflight"}
    review=dict(snapshot.get("package_review") or {})
    digest=str(review.get("review_digest") or "")
    con=get_connection()
    try:
        con.execute("PRAGMA query_only=ON")
        row=con.execute("SELECT * FROM jobs WHERE id=?",(job_id,)).fetchone()
        if row is None:
            return {"authorized":False,"reason":"job_missing"}
        job=dict(row)
    finally:
        con.close()
    admission=reserve_fresh_package(
        job=job,review_digest=digest,
        operation_created_at=str(operation["created_at"]),
    )
    if not admission.get("reserved"):
        return {"authorized":False,"reason":admission.get("reason")}
    # Core validates the same reservation and per-job Native lock before
    # minting. Failure uses up this one quota slot; never blindly retry it.
    result=flow.approve_and_submit_for_job(
        job_id,approval_source="STANDING_POLICY",
        consent_policy_id=admission["policy_id"],consent_slot=admission["slot"],
    )
    return {"authorized":True,"mandate_id":result["mandate_id"],
            "approval_source":"STANDING_POLICY",
            "submission_authority":False}
