"""Shared truth-binding primitives for strengthened Career OS Phases 4 and 5.

This module is local and inert. It binds generated resume versions and profile-
derived answer memories to the exact Hunter Candidate Truth Profile state without
changing browser, Apply, n8n, Gmail, provider, or submission authority.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from app import native_resume_service_v3 as v3
from app import profile_snapshot_projection
from app.database import get_connection
from app.tenant_foundation import current_owner

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QUESTION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,159}$")

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
        # Phase 4 and 5 base tables must exist before the sidecars.
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
    """Return the snapshot for the active Master Resume's latest confirmed profile.

    A newer active Master Resume without a confirmed profile blocks the strengthened
    engines instead of silently reusing truth from an older source.
    """
    connection = v3.v2.v1.get_connection()
    try:
        v3.ensure_schema(connection)
        owner = v3.v2.v1.current_owner(connection)
        source = v3.v2.v1.active_source(connection=connection)
        if not source:
            raise RuntimeError("Save a confirmed Master Resume source before using the strengthened engine.")
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
        extracted = v3._decode_profile_row(dict(row))
    finally:
        connection.close()
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
) -> None:
    connection = get_connection()
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
        connection.commit()
    finally:
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


def public_binding_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_extraction_id": snapshot["source_extraction_id"],
        "profile_revision": snapshot["profile_revision"],
        "profile_digest": snapshot["profile_digest"],
    }
