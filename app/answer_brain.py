"""Internal, feature-gated application answers; no UI, AI, Apply, or n8n path."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema

ANSWER_VERSION = "application-answer-v1"
ENCRYPTION_VERSION = "aes-gcm-v1"
KEY_VERSION = "env-v1"
QUESTION_FAMILIES = frozenset({"candidate_fact", "career_preference", "work_authorization", "salary", "location", "availability", "experience_skill", "voluntary_self_identification", "open_ended_job_specific", "credential_requirement", "unknown", "post_offer_sensitive"})
SENSITIVE_FAMILIES = frozenset({"voluntary_self_identification", "post_offer_sensitive"})
NON_PLAINTEXT_FAMILIES = SENSITIVE_FAMILIES | frozenset({"credential_requirement"})
NORMAL_QUESTION_FAMILIES = QUESTION_FAMILIES - NON_PLAINTEXT_FAMILIES
SOURCES = frozenset({"user", "profile_evidence", "ai_evidence"})
SELF_ID_CATEGORIES = frozenset({"veteran_status", "disability_status", "race_ethnicity", "gender_self_identification", "prefer_non_disclosure"})
SELF_ID_POLICIES = frozenset({"use_saved_response", "prefer_non_disclosure", "ask_each_time"})

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS application_answer_vault (
      answer_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
      question_family TEXT NOT NULL, canonical_answer TEXT NOT NULL, source TEXT NOT NULL,
      user_confirmed INTEGER NOT NULL CHECK(user_confirmed IN (0,1)),
      confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
      autofill_allowed INTEGER NOT NULL CHECK(autofill_allowed IN (0,1)),
      conditions_json TEXT NOT NULL DEFAULT '{}', last_confirmed_at TEXT,
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      UNIQUE(tenant_id,user_id,question_family,conditions_json),
      FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    "CREATE INDEX IF NOT EXISTS idx_answer_vault_owner ON application_answer_vault(tenant_id,user_id,question_family);",
    # SQLite cannot add a CHECK constraint to an existing table.  These
    # additive triggers defend both fresh and migrated databases from direct
    # SQL writes of sensitive or credential answers into the plaintext vault.
    """CREATE TRIGGER IF NOT EXISTS answer_vault_reject_non_plaintext_insert
       BEFORE INSERT ON application_answer_vault
       WHEN lower(NEW.question_family) IN ('voluntary_self_identification', 'post_offer_sensitive', 'credential_requirement')
       BEGIN SELECT RAISE(ABORT, 'non-plaintext answer family is not permitted in application_answer_vault'); END;""",
    """CREATE TRIGGER IF NOT EXISTS answer_vault_reject_non_plaintext_update
       BEFORE UPDATE OF question_family ON application_answer_vault
       WHEN lower(NEW.question_family) IN ('voluntary_self_identification', 'post_offer_sensitive', 'credential_requirement')
       BEGIN SELECT RAISE(ABORT, 'non-plaintext answer family is not permitted in application_answer_vault'); END;""",
    """CREATE TABLE IF NOT EXISTS sensitive_self_identification_vault (
      self_id_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, category TEXT NOT NULL,
      ciphertext BLOB NOT NULL, nonce BLOB NOT NULL, algorithm_version TEXT NOT NULL, key_version TEXT NOT NULL,
      autofill_policy TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_confirmed_at TEXT,
      UNIQUE(tenant_id,user_id,category),
      FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
)

class AnswerBrainError(RuntimeError):
    pass

def application_answer_brain_enabled() -> bool:
    return str(os.getenv("MUNSHI_APPLICATION_ANSWER_BRAIN_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}

def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    own = connection is None; connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS: connection.execute(statement)
        if own: connection.commit()
    finally:
        if own: connection.close()

def _owner(connection: sqlite3.Connection) -> OwnerContext:
    if not application_answer_brain_enabled(): raise RuntimeError("Application Answer Brain is disabled.")
    return current_owner(connection)

def _choice(value: str, allowed: frozenset[str], label: str) -> str:
    value = str(value or "").strip().casefold()
    if value not in allowed: raise ValueError(f"Unsupported {label}.")
    return value

def _conditions(value: Any) -> str:
    if value is None: value = {}
    if not isinstance(value, dict): raise ValueError("Conditions must be a JSON object.")
    result = json.dumps(value, sort_keys=True, separators=(",", ":"))
    if len(result) > 4000: raise ValueError("Conditions are too long.")
    return result

def _text(value: str, label: str = "Canonical answer") -> str:
    value = str(value or "").strip()
    if not value or len(value) > 8000: raise ValueError(f"{label} is required and bounded.")
    return value


def classify_question(question: str) -> str:
    """Conservatively map a bounded question to one ontology family.

    This is deliberately an allowlist classifier, not an LLM.  Ambiguous text
    remains ``unknown`` and therefore cannot be silently answered.
    """
    text = _text(question, "Question").casefold()
    if len(text) > 2_000:
        raise ValueError("Question is required and bounded.")
    if any(term in text for term in (
        "veteran", "disability", "race", "ethnicity", "gender identity",
        "self-identif", "prefer not to disclose", "prefer not to answer",
    )):
        return "voluntary_self_identification"
    if any(term in text for term in (
        "password", "passcode", "username", "log in", "login", "sign in",
        "security question", "one-time code", "otp", "authentication code",
        "bank account", "routing number",
    )):
        return "credential_requirement"
    if any(term in text for term in (
        "social security", "ssn", "passport", "date of birth", "background check",
    )):
        return "post_offer_sensitive"
    if any(term in text for term in ("authorized to work", "work authorization", "visa sponsorship", "sponsorship")):
        return "work_authorization"
    if any(term in text for term in ("salary", "compensation", "pay range", "hourly rate")):
        return "salary"
    if any(term in text for term in ("location", "where are you", "relocate", "remote", "hybrid", "onsite", "on-site")):
        return "location"
    if any(term in text for term in ("start date", "available to start", "when can you start", "availability", "notice period")):
        return "availability"
    if any(term in text for term in ("years of experience", "experience with", "proficien", "skill", "certification")):
        return "experience_skill"
    if any(term in text for term in ("career goal", "work preference", "preferred role", "job preference")):
        return "career_preference"
    if any(term in text for term in ("why are you", "cover letter", "describe a time", "why do you")):
        return "open_ended_job_specific"
    if any(term in text for term in ("full name", "phone number", "email address")):
        return "candidate_fact"
    return "unknown"

def _confidence(value: float) -> float:
    value = float(value)
    if not 0 <= value <= 1: raise ValueError("Confidence must be between 0 and 1.")
    return value

def _payload(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row); result["conditions"] = json.loads(result.pop("conditions_json"))
    result["user_confirmed"] = bool(result["user_confirmed"]); result["autofill_allowed"] = bool(result["autofill_allowed"])
    return result

def save_answer(*, question_family: str, canonical_answer: str, source: str, user_confirmed: bool, confidence: float, autofill_allowed: bool, conditions: dict[str, Any] | None = None) -> str:
    family = _choice(question_family, QUESTION_FAMILIES, "question family")
    if family in NON_PLAINTEXT_FAMILIES:
        raise ValueError("Sensitive and credential answers are not permitted in the normal answer vault.")
    source = _choice(source, SOURCES, "answer source"); answer = _text(canonical_answer); condition_json = _conditions(conditions)
    confirmed, allowed = bool(user_confirmed), bool(autofill_allowed)
    if allowed and not confirmed: raise ValueError("Autofill requires an explicitly confirmed answer.")
    if source == "user" and not confirmed: raise ValueError("User-sourced answers must be explicitly confirmed.")
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection)
        row = connection.execute("SELECT answer_id FROM application_answer_vault WHERE tenant_id=? AND user_id=? AND question_family=? AND conditions_json=?", (owner.tenant_id, owner.user_id, family, condition_json)).fetchone()
        answer_id = str(row["answer_id"]) if row else str(uuid4())
        confirmed_at = datetime.now(timezone.utc).isoformat() if confirmed else None
        connection.execute("""INSERT INTO application_answer_vault(answer_id,tenant_id,user_id,question_family,canonical_answer,source,user_confirmed,confidence,autofill_allowed,conditions_json,last_confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id,question_family,conditions_json) DO UPDATE SET canonical_answer=excluded.canonical_answer,source=excluded.source,user_confirmed=excluded.user_confirmed,confidence=excluded.confidence,autofill_allowed=excluded.autofill_allowed,last_confirmed_at=excluded.last_confirmed_at,updated_at=CURRENT_TIMESTAMP""", (answer_id,owner.tenant_id,owner.user_id,family,answer,source,int(confirmed),_confidence(confidence),int(allowed),condition_json,confirmed_at))
        connection.commit(); return answer_id
    finally: connection.close()

def normal_answer_projection() -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection)
        return [_payload(row) for row in connection.execute("SELECT * FROM application_answer_vault WHERE tenant_id=? AND user_id=? ORDER BY question_family,conditions_json", (owner.tenant_id,owner.user_id)).fetchall()]
    finally: connection.close()

def planning_input() -> dict[str, Any]:
    """Normal-answer-only boundary; self-ID never reaches a planner/model prompt."""
    return {"version": ANSWER_VERSION, "answers": normal_answer_projection()}

def resolve_answer(*, question_family: str, conditions: dict[str, Any] | None = None, profile_fact_key: str | None = None) -> dict[str, Any]:
    family = _choice(question_family, QUESTION_FAMILIES, "question family")
    if family in NON_PLAINTEXT_FAMILIES:
        return {"status":"NEEDS_INPUT", "reason":"non_plaintext_answer_requires_separate_policy"}
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); condition_json = _conditions(conditions)
        row = connection.execute("SELECT * FROM application_answer_vault WHERE tenant_id=? AND user_id=? AND question_family=? AND conditions_json=? AND user_confirmed=1 AND autofill_allowed=1", (owner.tenant_id,owner.user_id,family,condition_json)).fetchone()
        if row: return {"status":"ANSWERED", "resolution":"stored_verified", "answer":_payload(row)}
        if profile_fact_key and family in {"candidate_fact","experience_skill","work_authorization","availability","location"}:
            from app.candidate_digital_twin import ensure_schema as ensure_twin
            ensure_twin(connection)
            fact = connection.execute("""SELECT f.fact_key,f.value_json,f.provenance,f.confidence FROM candidate_digital_twin_facts f WHERE f.tenant_id=? AND f.user_id=? AND f.fact_key=? AND f.user_confirmed=1 AND EXISTS(SELECT 1 FROM candidate_digital_twin_evidence e WHERE e.fact_id=f.fact_id AND e.tenant_id=f.tenant_id AND e.user_id=f.user_id)""", (owner.tenant_id,owner.user_id,str(profile_fact_key).strip().casefold())).fetchone()
            if fact:
                return {"status":"ANSWERED", "resolution":"deterministic_profile_evidence", "answer":{
                    "canonical_answer":json.loads(fact["value_json"]), "source":"profile_evidence",
                    "evidence_provenance":fact["provenance"], "confidence":fact["confidence"],
                    "user_confirmed":True, "autofill_allowed":False, "fact_key":fact["fact_key"],
                }}
        return {"status":"NEEDS_INPUT", "reason":"no_safe_answer"}
    finally: connection.close()

def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as error: raise AnswerBrainError("Sensitive self-identification storage is unavailable.") from error

def _key() -> bytes:
    try: key = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_VAULT_KEY") or "").strip().encode("ascii"))
    except Exception as error: raise AnswerBrainError("Sensitive self-identification storage is not configured.") from error
    if len(key) != 32: raise AnswerBrainError("Sensitive self-identification storage is not configured.")
    return key

def _aad(owner: OwnerContext, record_id: str, category: str) -> bytes:
    return json.dumps({"category":category,"id":record_id,"owner":{"tenant_id":owner.tenant_id,"user_id":owner.user_id},"version":ENCRYPTION_VERSION},sort_keys=True,separators=(",",":")).encode()

def store_sensitive_self_identification(*, category: str, response: str | None = None, autofill_policy: str = "ask_each_time") -> str:
    """Store only a saved-response value; other policies store no response.

    ``ask_each_time`` and ``prefer_non_disclosure`` retain only their policy
    marker (encrypted empty payload), so a prior answer cannot surface later.
    """
    category = _choice(category,SELF_ID_CATEGORIES,"sensitive self-identification category")
    policy = _choice(autofill_policy,SELF_ID_POLICIES,"sensitive autofill policy")
    supplied = str(response or "").strip()
    if policy == "use_saved_response":
        plaintext = _text(supplied, "Sensitive response")
    elif supplied:
        raise ValueError("Only use_saved_response may retain a sensitive response.")
    else:
        plaintext = ""
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection)
        row = connection.execute("SELECT self_id_id FROM sensitive_self_identification_vault WHERE tenant_id=? AND user_id=? AND category=?",(owner.tenant_id,owner.user_id,category)).fetchone(); record_id = str(row["self_id_id"]) if row else str(uuid4())
        nonce=os.urandom(12); cipher=_aesgcm()(_key()).encrypt(nonce,plaintext.encode(),_aad(owner,record_id,category)); now=datetime.now(timezone.utc).isoformat()
        connection.execute("""INSERT INTO sensitive_self_identification_vault(self_id_id,tenant_id,user_id,category,ciphertext,nonce,algorithm_version,key_version,autofill_policy,last_confirmed_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,user_id,category) DO UPDATE SET ciphertext=excluded.ciphertext,nonce=excluded.nonce,algorithm_version=excluded.algorithm_version,key_version=excluded.key_version,autofill_policy=excluded.autofill_policy,last_confirmed_at=excluded.last_confirmed_at,updated_at=CURRENT_TIMESTAMP""",(record_id,owner.tenant_id,owner.user_id,category,cipher,nonce,ENCRYPTION_VERSION,KEY_VERSION,policy,now)); connection.commit(); return record_id
    finally: connection.close()

def read_sensitive_self_identification(*, category: str) -> str | None:
    category=_choice(category,SELF_ID_CATEGORIES,"sensitive self-identification category"); connection=get_connection()
    try:
        ensure_schema(connection); owner=_owner(connection); row=connection.execute("SELECT * FROM sensitive_self_identification_vault WHERE tenant_id=? AND user_id=? AND category=?",(owner.tenant_id,owner.user_id,category)).fetchone()
        if row is None or row["autofill_policy"] != "use_saved_response": return None
        if row["algorithm_version"] != ENCRYPTION_VERSION: raise AnswerBrainError("Sensitive self-identification record uses an unsupported encryption version.")
        try: return _aesgcm()(_key()).decrypt(bytes(row["nonce"]),bytes(row["ciphertext"]),_aad(owner,str(row["self_id_id"]),category)).decode()
        except Exception as error: raise AnswerBrainError("Sensitive self-identification record could not be decrypted.") from error
    finally: connection.close()


def resolve_sensitive_self_identification(*, category: str) -> dict[str, Any]:
    """Return a policy-safe response without exposing sensitive values to planners."""
    category = _choice(category, SELF_ID_CATEGORIES, "sensitive self-identification category")
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection)
        row = connection.execute(
            "SELECT * FROM sensitive_self_identification_vault WHERE tenant_id=? AND user_id=? AND category=?",
            (owner.tenant_id, owner.user_id, category),
        ).fetchone()
        if row is None:
            return {"status": "NEEDS_INPUT", "reason": "no_sensitive_policy"}
        policy = str(row["autofill_policy"])
        if policy == "ask_each_time":
            return {"status": "NEEDS_INPUT", "reason": "ask_each_time"}
        if policy == "prefer_non_disclosure":
            return {"status": "ANSWERED", "resolution": "prefer_non_disclosure", "answer": "Prefer not to disclose"}
        # Do the authenticated decrypt on this owner-scoped connection.  Do
        # not recursively open a second schema-initializing connection here:
        # SQLite DDL may otherwise wait on this connection's active read.
        if policy == "use_saved_response":
            if row["algorithm_version"] != ENCRYPTION_VERSION:
                raise AnswerBrainError("Sensitive self-identification record uses an unsupported encryption version.")
            try:
                value = _aesgcm()(_key()).decrypt(
                    bytes(row["nonce"]), bytes(row["ciphertext"]),
                    _aad(owner, str(row["self_id_id"]), category),
                ).decode()
            except Exception as error:
                raise AnswerBrainError("Sensitive self-identification record could not be decrypted.") from error
            return {"status": "ANSWERED", "resolution": "stored_sensitive_response", "answer": value}
        raise AnswerBrainError("Sensitive self-identification policy is unsupported.")
    finally:
        connection.close()

def clear_sensitive_self_identification(*, category: str) -> bool:
    category=_choice(category,SELF_ID_CATEGORIES,"sensitive self-identification category"); connection=get_connection()
    try:
        ensure_schema(connection); owner=_owner(connection); cursor=connection.execute("DELETE FROM sensitive_self_identification_vault WHERE tenant_id=? AND user_id=? AND category=?",(owner.tenant_id,owner.user_id,category)); connection.commit(); return bool(cursor.rowcount)
    finally: connection.close()
