"""Shared truth-binding primitives for strengthened Career OS Phases 4 and 5.

This module is local and inert. It binds generated resume versions and profile-
derived answer memories to the exact Hunter Candidate Truth Profile state without
changing browser, Apply, n8n, Gmail, provider, or submission authority.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from app import native_resume_service_v3 as v3
from app import profile_snapshot_projection
from app.database import get_connection
from app.tenant_foundation import current_owner, current_owner_readonly

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUESTION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,159}$")
RESUME_TRUTH_READ_SET_VERSION = "munshi-resume-truth-read-set-v1"

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_truth_bindings (
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        source_profile_sha256 TEXT NOT NULL CHECK(length(source_profile_sha256)=64),
        source_resume_sha256 TEXT NOT NULL CHECK(length(source_resume_sha256)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_truth_bindings_owner
       ON native_resume_truth_bindings(tenant_id,user_id,source_extraction_id,profile_revision);""",
    """CREATE TABLE IF NOT EXISTS native_resume_truth_read_sets (
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        read_set_version TEXT NOT NULL,
        projection_version TEXT NOT NULL,
        read_set_json TEXT NOT NULL,
        read_set_digest TEXT NOT NULL CHECK(length(read_set_digest)=64),
        binding_mode TEXT NOT NULL CHECK(binding_mode IN ('GENERATED','SAFE_BACKFILL')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_truth_read_sets_owner
       ON native_resume_truth_read_sets(tenant_id,user_id,created_at DESC);""",
    """CREATE TABLE IF NOT EXISTS application_answer_truth_bindings (
        answer_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        question_key TEXT NOT NULL,
        profile_fact_key TEXT NOT NULL,
        source_extraction_id TEXT NOT NULL,
        profile_revision INTEGER NOT NULL CHECK(profile_revision >= 1),
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(answer_id) REFERENCES application_answer_vault(answer_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_application_answer_truth_bindings_owner
       ON application_answer_truth_bindings(tenant_id,user_id,question_key,profile_revision);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        from app import native_resume_service_v2
        from app import answer_brain

        native_resume_service_v2.ensure_schema(connection)
        answer_brain.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def canonical_question_key(value: str) -> str:
    key = str(value or "").strip().casefold()
    if not QUESTION_KEY_RE.fullmatch(key):
        raise ValueError(
            "Question key must be a stable lowercase semantic key using letters, numbers, '.', '_', ':', or '-'."
        )
    return key


def _digest(value: Any, label: str) -> str:
    result = str(value or "").strip()
    if not SHA256_RE.fullmatch(result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest.")
    return result


def current_candidate_profile_snapshot() -> dict[str, Any]:
    """Return the active confirmed profile through a strictly read-only path."""
    connection = v3.v2.v1.get_connection()
    row_payload: dict[str, Any] | None = None
    try:
        owner = current_owner_readonly(connection)
        source = connection.execute(
            """SELECT source_id,label,source_kind,content_sha256,created_at,updated_at
                 FROM native_resume_sources
                WHERE tenant_id=? AND user_id=? AND active=1
                ORDER BY updated_at DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        if source is None:
            raise RuntimeError(
                "Save a confirmed Master Resume source before using the strengthened engine."
            )
        row = connection.execute(
            """SELECT * FROM native_resume_profile_extracts
               WHERE tenant_id=? AND user_id=? AND source_id=? AND status='CONFIRMED'
               ORDER BY confirmed_at DESC,created_at DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, str(source["source_id"])),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Confirm the Candidate Truth Profile for the current Master Resume before using the strengthened engine."
            )
        row_payload = dict(row)
    finally:
        connection.close()

    assert row_payload is not None
    extracted = v3._decode_profile_row(row_payload)
    return profile_snapshot_projection.build_candidate_profile_snapshot(extracted)


def safe_resume_profile_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return only non-protected resume-relevant profile facts for the writer prompt."""
    allowed_prefixes = (
        "profile.professional_summary",
        "contact.",
        "education.",
        "experience.",
        "projects.",
        "skills.",
        "certifications.",
        "languages",
    )
    facts: list[dict[str, Any]] = []
    for fact in snapshot.get("facts") or []:
        key = str(fact.get("key") or "")
        if fact.get("protected") is True:
            continue
        if not any(key == prefix or key.startswith(prefix) for prefix in allowed_prefixes):
            continue
        facts.append(
            {
                "fact_id": fact["fact_id"],
                "key": key,
                "category": fact["category"],
                "trust_level": fact["trust_level"],
                "source": fact["source"],
                "value": fact["value"],
            }
        )
    return {
        "source_extraction_id": snapshot["source_extraction_id"],
        "profile_revision": snapshot["profile_revision"],
        "profile_digest": snapshot["profile_digest"],
        "facts": facts,
    }


def save_resume_truth_binding(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    snapshot: dict[str, Any],
) -> None:
    ensure_schema(connection)
    owner = current_owner(connection)
    if snapshot.get("tenant_id") != owner.tenant_id or snapshot.get("user_id") != owner.user_id:
        raise ValueError("Resume truth binding owner does not match the active candidate.")
    connection.execute(
        """INSERT INTO native_resume_truth_bindings(
               version_id,tenant_id,user_id,source_extraction_id,profile_revision,
               profile_digest,source_profile_sha256,source_resume_sha256
           ) VALUES (?,?,?,?,?,?,?,?)""",
        (
            str(version_id), owner.tenant_id, owner.user_id,
            str(snapshot["source_extraction_id"]), int(snapshot["profile_revision"]),
            _digest(snapshot["profile_digest"], "Profile digest"),
            _digest(snapshot["source_profile_sha256"], "Source profile digest"),
            _digest(snapshot["source_resume_sha256"], "Source resume digest"),
        ),
    )


def resume_truth_binding(
    version_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM native_resume_truth_bindings
               WHERE version_id=? AND tenant_id=? AND user_id=?""",
            (str(version_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        if owns:
            connection.close()


def _fact_value_digest(fact: Mapping[str, Any]) -> str:
    if fact.get("protected") is True or "value" not in fact:
        raise ValueError("Resume truth read sets may contain only non-protected value facts.")
    payload = {
        "fact_id": str(fact.get("fact_id") or ""),
        "key": str(fact.get("key") or ""),
        "value": fact.get("value"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _document_truth_evidence_ids(document: Mapping[str, Any]) -> list[str]:
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key) == "evidence_ids" and isinstance(nested, list):
                    for raw in nested:
                        token = str(raw or "").strip()
                        if token.startswith("truth:") and len(token) > len("truth:"):
                            found.add(token[len("truth:"):])
                else:
                    walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(document)
    return sorted(found)


def _read_set_payload(
    *,
    snapshot: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, str]:
    facts = {
        str(fact.get("fact_id") or ""): dict(fact)
        for fact in snapshot.get("facts") or []
        if isinstance(fact, Mapping) and str(fact.get("fact_id") or "")
    }
    consumed = _document_truth_evidence_ids(document)
    read_set: dict[str, str] = {}
    for fact_id in consumed:
        fact = facts.get(fact_id)
        if fact is None:
            raise RuntimeError(
                "Resume consumed Candidate Truth evidence that is absent from the bound snapshot."
            )
        read_set[fact_id] = _fact_value_digest(fact)
    return read_set


def save_resume_truth_read_set(
    connection: sqlite3.Connection,
    *,
    version_id: str,
    snapshot: Mapping[str, Any],
    document: Mapping[str, Any],
    binding_mode: str = "GENERATED",
) -> dict[str, Any]:
    ensure_schema(connection)
    owner = current_owner(connection)
    if snapshot.get("tenant_id") != owner.tenant_id or snapshot.get("user_id") != owner.user_id:
        raise ValueError("Resume truth read-set owner does not match the active candidate.")
    mode = str(binding_mode or "").strip().upper()
    if mode not in {"GENERATED", "SAFE_BACKFILL"}:
        raise ValueError("Unsupported resume truth read-set binding mode.")
    read_set = _read_set_payload(snapshot=snapshot, document=document)
    payload = {
        "read_set_version": RESUME_TRUTH_READ_SET_VERSION,
        "projection_version": str(snapshot.get("contract_version") or ""),
        "read_set": read_set,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    connection.execute(
        """INSERT INTO native_resume_truth_read_sets(
               version_id,tenant_id,user_id,read_set_version,projection_version,
               read_set_json,read_set_digest,binding_mode
           ) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(version_id) DO NOTHING""",
        (
            str(version_id),
            owner.tenant_id,
            owner.user_id,
            RESUME_TRUTH_READ_SET_VERSION,
            str(snapshot.get("contract_version") or ""),
            json.dumps(read_set, sort_keys=True, separators=(",", ":")),
            digest,
            mode,
        ),
    )
    row = connection.execute(
        """SELECT * FROM native_resume_truth_read_sets
           WHERE version_id=? AND tenant_id=? AND user_id=?""",
        (str(version_id), owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Resume truth read set was not persisted.")
    result = dict(row)
    result["read_set"] = json.loads(str(result.pop("read_set_json")))
    return result


def resume_truth_read_set(
    version_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM native_resume_truth_read_sets
               WHERE version_id=? AND tenant_id=? AND user_id=?""",
            (str(version_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            return {}
        result = dict(row)
        result["read_set"] = json.loads(str(result.pop("read_set_json")))
        return result
    finally:
        if owns:
            connection.close()


def _composite_revision_parts(profile_revision: int) -> tuple[int, int]:
    """Invert profile_snapshot_projection._composite_revision exactly."""
    paired = int(profile_revision) - 1
    if paired < 0:
        raise ValueError("Candidate Truth profile revision must be positive.")
    width = (math.isqrt(8 * paired + 1) - 1) // 2
    triangle = width * (width + 1) // 2
    details_revision = paired - triangle
    override_revision = width - details_revision
    if override_revision < 0 or details_revision < 0:
        raise ValueError("Candidate Truth profile revision is invalid.")
    return override_revision, details_revision


def _resume_document(
    version_id: str,
    *,
    connection: sqlite3.Connection,
) -> dict[str, Any]:
    owner = current_owner(connection)
    row = connection.execute(
        """SELECT document_json FROM native_resume_versions
           WHERE version_id=? AND tenant_id=? AND user_id=?""",
        (str(version_id), owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Resume version does not belong to the current candidate.")
    document = json.loads(str(row["document_json"]))
    if not isinstance(document, dict):
        raise RuntimeError("Stored resume document is invalid.")
    return document


def _safe_backfill_allowed(
    *,
    binding: Mapping[str, Any],
    current_snapshot: Mapping[str, Any],
) -> bool:
    if not binding:
        return False
    if str(binding.get("source_extraction_id") or "") != str(
        current_snapshot.get("source_extraction_id") or ""
    ):
        return False
    if str(binding.get("source_profile_sha256") or "") != str(
        current_snapshot.get("source_profile_sha256") or ""
    ):
        return False
    if str(binding.get("source_resume_sha256") or "") != str(
        current_snapshot.get("source_resume_sha256") or ""
    ):
        return False
    old_override_revision, _ = _composite_revision_parts(
        int(binding.get("profile_revision") or 0)
    )
    current_override_revision = int(current_snapshot.get("override_revision") or 0)
    return old_override_revision == current_override_revision


def ensure_resume_truth_read_set(
    version_id: str,
    *,
    current_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    existing = resume_truth_read_set(version_id)
    if existing:
        return existing
    snapshot = dict(current_snapshot or current_candidate_profile_snapshot())
    binding = resume_truth_binding(version_id)
    if not _safe_backfill_allowed(binding=binding, current_snapshot=snapshot):
        raise RuntimeError(
            "Historical resume read set cannot be reconstructed safely from current truth."
        )
    connection = get_connection()
    try:
        ensure_schema(connection)
        document = _resume_document(version_id, connection=connection)
        result = save_resume_truth_read_set(
            connection,
            version_id=version_id,
            snapshot=snapshot,
            document=document,
            binding_mode="SAFE_BACKFILL",
        )
        connection.commit()
        return result
    finally:
        connection.close()


def resume_binding_is_fresh(
    version_id: str,
    current_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = dict(current_snapshot or current_candidate_profile_snapshot())
    try:
        binding = ensure_resume_truth_read_set(
            version_id,
            current_snapshot=snapshot,
        )
    except (LookupError, RuntimeError, ValueError) as error:
        return {
            "fresh": False,
            "reason": "READ_SET_UNAVAILABLE",
            "detail": str(error),
            "version_id": str(version_id),
        }

    if str(binding.get("read_set_version") or "") != RESUME_TRUTH_READ_SET_VERSION:
        return {
            "fresh": False,
            "reason": "READ_SET_VERSION_CHANGED",
            "version_id": str(version_id),
        }
    if str(binding.get("projection_version") or "") != str(
        snapshot.get("contract_version") or ""
    ):
        return {
            "fresh": False,
            "reason": "PROJECTION_VERSION_CHANGED",
            "version_id": str(version_id),
        }

    current_facts = {
        str(fact.get("fact_id") or ""): dict(fact)
        for fact in snapshot.get("facts") or []
        if isinstance(fact, Mapping) and str(fact.get("fact_id") or "")
    }
    changed: list[str] = []
    missing: list[str] = []
    for fact_id, expected_digest in dict(binding.get("read_set") or {}).items():
        fact = current_facts.get(str(fact_id))
        if fact is None:
            missing.append(str(fact_id))
            continue
        try:
            actual_digest = _fact_value_digest(fact)
        except ValueError:
            changed.append(str(fact_id))
            continue
        if actual_digest != str(expected_digest):
            changed.append(str(fact_id))

    fresh = not changed and not missing
    return {
        "fresh": fresh,
        "reason": "MATCH" if fresh else "CONSUMED_TRUTH_CHANGED",
        "version_id": str(version_id),
        "read_set_digest": str(binding.get("read_set_digest") or ""),
        "binding_mode": str(binding.get("binding_mode") or ""),
        "changed_fact_ids": changed,
        "missing_fact_ids": missing,
    }


def assert_parent_truth_current(parent_version_id: str, snapshot: dict[str, Any]) -> None:
    binding = resume_truth_binding(parent_version_id)
    if not binding:
        raise ValueError(
            "This older resume version is not Candidate-Truth-bound. Start a fresh strengthened resume before revising it."
        )
    if (
        binding["source_extraction_id"] != snapshot["source_extraction_id"]
        or int(binding["profile_revision"]) != int(snapshot["profile_revision"])
        or binding["profile_digest"] != snapshot["profile_digest"]
    ):
        raise ValueError(
            "Candidate Truth Profile changed after this resume was created. Start a fresh resume from the current profile."
        )


def profile_fact(snapshot: dict[str, Any], fact_key: str) -> dict[str, Any] | None:
    key = str(fact_key or "").strip().casefold()
    if not key:
        return None
    for fact in snapshot.get("facts") or []:
        if str(fact.get("key") or "").casefold() == key:
            return dict(fact)
    return None


def save_answer_truth_binding(
    *,
    answer_id: str,
    question_key: str,
    profile_fact_key: str,
    snapshot: dict[str, Any],
    connection: sqlite3.Connection | None = None,
) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if snapshot.get("tenant_id") != owner.tenant_id or snapshot.get("user_id") != owner.user_id:
            raise ValueError("Answer truth binding owner does not match the active candidate.")
        connection.execute(
            """INSERT INTO application_answer_truth_bindings(
                   answer_id,tenant_id,user_id,question_key,profile_fact_key,
                   source_extraction_id,profile_revision,profile_digest
               ) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(answer_id) DO UPDATE SET
                   question_key=excluded.question_key,
                   profile_fact_key=excluded.profile_fact_key,
                   source_extraction_id=excluded.source_extraction_id,
                   profile_revision=excluded.profile_revision,
                   profile_digest=excluded.profile_digest,
                   updated_at=CURRENT_TIMESTAMP""",
            (
                str(answer_id), owner.tenant_id, owner.user_id,
                canonical_question_key(question_key), str(profile_fact_key).strip().casefold(),
                str(snapshot["source_extraction_id"]), int(snapshot["profile_revision"]),
                _digest(snapshot["profile_digest"], "Profile digest"),
            ),
        )
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def answer_truth_binding(answer_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT * FROM application_answer_truth_bindings
               WHERE answer_id=? AND tenant_id=? AND user_id=?""",
            (str(answer_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        connection.close()


def binding_matches_snapshot(binding: dict[str, Any], snapshot: dict[str, Any]) -> bool:
    return bool(binding) and (
        str(binding.get("source_extraction_id")) == str(snapshot.get("source_extraction_id"))
        and int(binding.get("profile_revision") or 0) == int(snapshot.get("profile_revision") or 0)
        and str(binding.get("profile_digest")) == str(snapshot.get("profile_digest"))
    )


def resume_relevant_binding_matches(
    binding: Mapping[str, Any],
    snapshot: Mapping[str, Any],
) -> bool:
    """Ignore application-detail-only revisions for resume/JD freshness.

    Resume/JD consumers read the immutable resume source plus resume-profile
    overrides. Candidate application details are intentionally outside that
    read domain. Any source/extraction change or resume-override revision change
    still fails closed.
    """
    if not binding:
        return False
    if binding_matches_snapshot(dict(binding), dict(snapshot)):
        return True
    if str(binding.get("source_extraction_id") or "") != str(
        snapshot.get("source_extraction_id") or ""
    ):
        return False
    try:
        old_override_revision, _ = _composite_revision_parts(
            int(binding.get("profile_revision") or 0)
        )
    except ValueError:
        return False
    return old_override_revision == int(snapshot.get("override_revision") or 0)


def public_binding_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_extraction_id": snapshot["source_extraction_id"],
        "profile_revision": snapshot["profile_revision"],
        "profile_digest": snapshot["profile_digest"],
    }
