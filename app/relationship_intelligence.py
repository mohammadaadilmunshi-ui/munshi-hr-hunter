"""Feature-gated, tenant-owned relationship intelligence ledger.

This is deliberately a local evidence index, not an enrichment or outreach
engine.  It neither discovers contacts nor sends messages.  Shared Hunter jobs
are linked only after an authoritative owner association exists, preventing a
tenant from using a guessed shared job id to read another owner's linkage.
"""
from __future__ import annotations

import os
import re
import sqlite3
from typing import Any, Mapping
from urllib.parse import urlparse
from uuid import uuid4

from app.database import get_connection
from app.tenant_foundation import OwnerContext, current_owner, ensure_schema as ensure_tenant_schema

RELATIONSHIP_INTELLIGENCE_VERSION = "relationship-intelligence-v1"
CONTACT_TYPES = frozenset({"recruiter", "ta_partner", "hiring_manager", "hrbp", "team_lead", "department_lead", "alumni", "mutual_connection", "relevant_employee"})
SOURCES = frozenset({"public_company", "public_profile", "existing_contact_finder", "user_supplied"})
RECOMMENDED_ACTIONS = frozenset({"review", "connect", "request_introduction", "no_action"})
EMAIL_PROVENANCE = frozenset({"explicit_contact_email", "inferred_pattern", "not_provided"})
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_PATTERN_DOMAIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_PATTERN_TOKENS = ("{first}", "{last}", "{first_initial}", "{last_initial}", "{f}", "{l}", "first", "last", "initial")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS relationship_contacts (
        contact_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        display_name TEXT NOT NULL, company_name TEXT, title TEXT,
        contact_type TEXT NOT NULL, relevance TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        source TEXT NOT NULL, recommended_action TEXT NOT NULL,
        email_value TEXT, email_provenance TEXT NOT NULL DEFAULT 'not_provided',
        inferred_email_pattern TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,contact_id),
        CHECK(email_provenance IN ('explicit_contact_email','inferred_pattern','not_provided')),
        CHECK((email_value IS NULL AND email_provenance != 'explicit_contact_email') OR (email_value IS NOT NULL AND email_provenance = 'explicit_contact_email')),
        CHECK((inferred_email_pattern IS NULL AND email_provenance != 'inferred_pattern') OR (inferred_email_pattern IS NOT NULL AND email_provenance = 'inferred_pattern'))
    );""",
    "CREATE INDEX IF NOT EXISTS idx_relationship_contacts_owner ON relationship_contacts(tenant_id,user_id,created_at DESC);",
    """CREATE TABLE IF NOT EXISTS relationship_contact_evidence (
        evidence_id TEXT PRIMARY KEY, contact_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
        source TEXT NOT NULL, evidence_url TEXT, evidence_summary TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id,user_id,contact_id) REFERENCES relationship_contacts(tenant_id,user_id,contact_id) ON DELETE CASCADE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_relationship_evidence_owner_contact ON relationship_contact_evidence(tenant_id,user_id,contact_id);",
    """CREATE TABLE IF NOT EXISTS relationship_contact_job_links (
        contact_id TEXT NOT NULL, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL, job_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(contact_id,tenant_id,user_id,job_id),
        FOREIGN KEY (tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY (tenant_id,user_id,contact_id) REFERENCES relationship_contacts(tenant_id,user_id,contact_id) ON DELETE CASCADE
    );""",
    "CREATE INDEX IF NOT EXISTS idx_relationship_job_links_owner_job ON relationship_contact_job_links(tenant_id,user_id,job_id);",
)


def relationship_intelligence_enabled() -> bool:
    return str(os.getenv("MUNSHI_RELATIONSHIP_INTELLIGENCE_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    own = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if own:
            connection.commit()
    finally:
        if own:
            connection.close()


def _owner(connection: sqlite3.Connection) -> OwnerContext:
    if not relationship_intelligence_enabled():
        raise RuntimeError("Relationship intelligence is disabled.")
    return current_owner(connection)


def _text(value: Any, label: str, *, maximum: int = 500, required: bool = True) -> str | None:
    result = str(value or "").strip()
    if not result:
        if required:
            raise ValueError(f"{label} is required.")
        return None
    if len(result) > maximum:
        raise ValueError(f"{label} is too long.")
    return result


def _choice(value: Any, choices: frozenset[str], label: str) -> str:
    result = str(value or "").strip().casefold()
    if result not in choices:
        raise ValueError(f"Unsupported {label}.")
    return result


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Confidence must be numeric.") from error
    if not 0 <= result <= 1:
        raise ValueError("Confidence must be between 0 and 1.")
    return result


def _url(value: Any, label: str, *, required: bool = False) -> str | None:
    result = _text(value, label, maximum=2000, required=required)
    if result is None:
        return None
    parsed = urlparse(result)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} must be a public http(s) URL.")
    return result


def _email_fields(values: Mapping[str, Any]) -> tuple[str | None, str, str | None]:
    email = _text(values.get("email"), "Email", maximum=320, required=False)
    provenance = _choice(values.get("email_provenance", "not_provided"), EMAIL_PROVENANCE, "email provenance")
    pattern = _text(values.get("inferred_email_pattern"), "Inferred email pattern", maximum=320, required=False)
    if email is not None and (provenance != "explicit_contact_email" or not _EMAIL.fullmatch(email)):
        raise ValueError("Only an explicitly supplied valid contact email may be stored.")
    if provenance == "explicit_contact_email" and email is None:
        raise ValueError("Explicit email provenance requires an email.")
    if provenance == "inferred_pattern":
        if pattern is None or email is not None:
            raise ValueError("An inferred pattern must be labelled and cannot store an email value.")
        local, separator, domain = pattern.partition("@")
        if (
            separator != "@"
            or "@" in domain
            or not local
            or not _PATTERN_DOMAIN.fullmatch(domain)
            or not any(token in local.casefold() for token in _PATTERN_TOKENS)
        ):
            raise ValueError("Inferred email pattern must contain a name placeholder and a valid domain.")
    elif pattern is not None:
        raise ValueError("An inferred email pattern requires inferred_pattern provenance.")
    return email, provenance, pattern


def save_contact(values: Mapping[str, Any]) -> str:
    """Persist explicitly supplied contact metadata; never performs enrichment."""
    if not isinstance(values, Mapping):
        raise ValueError("Contact must be an object.")
    allowed = {"display_name", "company_name", "title", "contact_type", "relevance", "confidence", "source", "recommended_action", "email", "email_provenance", "inferred_email_pattern"}
    if set(values) - allowed:
        raise ValueError("Unsupported contact fields.")
    email, email_provenance, pattern = _email_fields(values)
    payload = (
        _text(values.get("display_name"), "Display name"), _text(values.get("company_name"), "Company", required=False),
        _text(values.get("title"), "Title", required=False), _choice(values.get("contact_type"), CONTACT_TYPES, "contact type"),
        _text(values.get("relevance"), "Relevance", maximum=2000), _confidence(values.get("confidence")),
        _choice(values.get("source"), SOURCES, "source"), _choice(values.get("recommended_action"), RECOMMENDED_ACTIONS, "recommended action"),
        email, email_provenance, pattern,
    )
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); contact_id = str(uuid4())
        connection.execute("""INSERT INTO relationship_contacts(contact_id,tenant_id,user_id,display_name,company_name,title,contact_type,relevance,confidence,source,recommended_action,email_value,email_provenance,inferred_email_pattern) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (contact_id, owner.tenant_id, owner.user_id, *payload))
        connection.commit()
        return contact_id
    finally:
        connection.close()


def add_evidence(*, contact_id: str, source: str, evidence_summary: str, confidence: float, evidence_url: str | None = None) -> str:
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); contact_id = str(_text(contact_id, "Contact id", maximum=120))
        exists = connection.execute("SELECT 1 FROM relationship_contacts WHERE contact_id=? AND tenant_id=? AND user_id=?", (contact_id, owner.tenant_id, owner.user_id)).fetchone()
        if exists is None:
            raise LookupError("Contact is not owned by the current user.")
        evidence_id = str(uuid4())
        connection.execute("INSERT INTO relationship_contact_evidence(evidence_id,contact_id,tenant_id,user_id,source,evidence_url,evidence_summary,confidence) VALUES (?,?,?,?,?,?,?,?)", (evidence_id, contact_id, owner.tenant_id, owner.user_id, _choice(source, SOURCES, "source"), _url(evidence_url, "Evidence URL"), _text(evidence_summary, "Evidence summary", maximum=4000), _confidence(confidence)))
        connection.commit(); return evidence_id
    finally:
        connection.close()


def link_contact_to_job(*, contact_id: str, job_id: int) -> None:
    """Link to a shared job only if an upstream authority assigned its owner."""
    try:
        job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    if job_id <= 0:
        raise ValueError("Job id must be positive.")
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); contact_id = str(_text(contact_id, "Contact id", maximum=120))
        contact = connection.execute("SELECT 1 FROM relationship_contacts WHERE contact_id=? AND tenant_id=? AND user_id=?", (contact_id, owner.tenant_id, owner.user_id)).fetchone()
        owned_job = connection.execute("SELECT 1 FROM owned_record_owners WHERE record_domain='job' AND record_key=? AND tenant_id=? AND user_id=?", (str(job_id), owner.tenant_id, owner.user_id)).fetchone()
        job = connection.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
        if contact is None or job is None or owned_job is None:
            raise LookupError("Contact or defensively associated job is unavailable to the current user.")
        connection.execute("INSERT OR IGNORE INTO relationship_contact_job_links(contact_id,tenant_id,user_id,job_id) VALUES (?,?,?,?)", (contact_id, owner.tenant_id, owner.user_id, job_id))
        connection.commit()
    finally:
        connection.close()


def contacts_for_job(*, job_id: int) -> list[dict[str, Any]]:
    try:
        job_id = int(job_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Job id must be an integer.") from error
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection)
        rows = connection.execute("""SELECT c.* FROM relationship_contacts c JOIN relationship_contact_job_links l ON l.contact_id=c.contact_id AND l.tenant_id=c.tenant_id AND l.user_id=c.user_id WHERE l.job_id=? AND c.tenant_id=? AND c.user_id=? ORDER BY c.created_at DESC""", (job_id, owner.tenant_id, owner.user_id)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def contact_evidence(contact_id: str) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); contact_id = str(_text(contact_id, "Contact id", maximum=120))
        rows = connection.execute("SELECT * FROM relationship_contact_evidence WHERE contact_id=? AND tenant_id=? AND user_id=? ORDER BY created_at", (contact_id, owner.tenant_id, owner.user_id)).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def contact_information_state(contact_id: str) -> dict[str, str]:
    """Return explicit, non-speculative labels for one owned contact record.

    These labels deliberately describe what is known, supplied, observed,
    inferred, or still unknown.  They never upgrade a pattern into a contact
    email and the owner predicate prevents this summary becoming a discovery
    API across tenants.
    """
    connection = get_connection()
    try:
        ensure_schema(connection); owner = _owner(connection); contact_id = str(_text(contact_id, "Contact id", maximum=120))
        row = connection.execute(
            "SELECT source,email_provenance FROM relationship_contacts WHERE contact_id=? AND tenant_id=? AND user_id=?",
            (contact_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Contact is not owned by the current user.")
        return {
            "contact": "known_contact",
            "relationship": "supplied_evidence" if row["source"] == "user_supplied" else "observed_relationship",
            "email": {
                "explicit_contact_email": "known_contact_email",
                "inferred_pattern": "inferred_pattern",
                "not_provided": "unknown_unverified",
            }[row["email_provenance"]],
        }
    finally:
        connection.close()
