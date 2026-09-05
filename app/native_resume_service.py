"""Tenant-safe native Resume Studio service.

This service is additive and staging-safe. It stores candidate-confirmed resume
source text, builds an evidence bundle, asks an explicitly configured OpenAI
Responses API model for a structured ResumeDocument, validates every evidence
reference, rejects unsupported numeric claims, computes explainable ATS/JD
diagnostics, and persists immutable resume versions.

It does not submit applications, change n8n authority, or overwrite the Master
Resume designation. Native resume authority remains disabled until a later
explicit parity gate.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4
from xml.etree import ElementTree
from xml.sax.saxutils import escape as xml_escape

import httpx

from app.database import get_connection
from app.native_resume_studio import (
    ResumeDocument,
    ats_readiness_issues,
    document_word_count,
    evidence_ids as resume_evidence_ids,
    render_ats_html,
)
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema


MODEL_ENV = "MUNSHI_RESUME_MODEL"
API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_MODEL = "gpt-5.6-terra"
SCHEMA_VERSION = "native-resume-studio-service-v1"
_MAX_SOURCE_CHARS = 80_000
_MAX_INSTRUCTION_CHARS = 2_000
_MAX_EVIDENCE_ITEMS = 180
_MAX_EVIDENCE_TEXT = 1_200
_SENSITIVE_TOKENS = frozenset({
    "age", "birth", "citizen", "citizenship", "disability", "ethnicity",
    "gender", "marital", "race", "religion", "sex", "veteran",
})
_STOP_TERMS = frozenset({
    "about", "after", "also", "and", "are", "been", "being", "but", "can",
    "company", "for", "from", "have", "into", "job", "our", "role", "that",
    "the", "their", "this", "with", "will", "you", "your", "years", "work",
    "working", "team", "teams", "responsibilities", "requirements",
    "qualifications", "preferred", "including", "required",
})
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9+/#.-]{2,}")
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%|\+)?")
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/#.-]+")


SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_resume_sources(
        source_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        label TEXT NOT NULL,
        source_kind TEXT NOT NULL CHECK(source_kind IN ('pasted_text','text_upload','docx_upload')),
        content_sha256 TEXT NOT NULL,
        content_text TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,content_sha256),
        UNIQUE(tenant_id,user_id,source_id),
        FOREIGN KEY(tenant_id,user_id)
            REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_sources_owner_active
       ON native_resume_sources(tenant_id,user_id,active,updated_at DESC);""",
    """CREATE TABLE IF NOT EXISTS native_resume_versions(
        version_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        source_id TEXT NOT NULL,
        parent_version_id TEXT,
        version_number INTEGER NOT NULL,
        instruction TEXT NOT NULL DEFAULT '',
        locked_sections_json TEXT NOT NULL DEFAULT '[]',
        model_name TEXT NOT NULL,
        model_response_id TEXT,
        evidence_digest TEXT NOT NULL,
        document_json TEXT NOT NULL,
        diagnostics_json TEXT NOT NULL,
        html_sha256 TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('VALIDATED')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(tenant_id,user_id,job_id,version_number),
        UNIQUE(tenant_id,user_id,version_id),
        FOREIGN KEY(tenant_id,user_id)
            REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id,source_id)
            REFERENCES native_resume_sources(tenant_id,user_id,source_id) ON DELETE RESTRICT,
        FOREIGN KEY(parent_version_id) REFERENCES native_resume_versions(version_id) ON DELETE RESTRICT,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_resume_versions_owner_job
       ON native_resume_versions(tenant_id,user_id,job_id,created_at DESC);""",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def native_resume_authority_enabled() -> bool:
    """The native writer is not application authority in this pre-Phase-12 slice."""
    return False


def model_status() -> dict[str, Any]:
    return {
        "configured": bool(str(os.getenv(API_KEY_ENV) or "").strip()),
        "model": str(os.getenv(MODEL_ENV) or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        "provider": "OpenAI Responses API",
        "native_authority": False,
    }


def _clean_text(value: str, *, maximum: int, label: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    if len(text) > maximum:
        raise ValueError(f"{label} must be at most {maximum:,} characters.")
    return text


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def save_confirmed_source(
    *,
    content_text: str,
    label: str = "Confirmed resume source",
    source_kind: str = "pasted_text",
) -> dict[str, Any]:
    text = _clean_text(content_text, maximum=_MAX_SOURCE_CHARS, label="Resume source")
    kind = str(source_kind or "").strip().casefold()
    if kind not in {"pasted_text", "text_upload", "docx_upload"}:
        raise ValueError("Unsupported resume source kind.")
    display = " ".join(str(label or "Confirmed resume source").split())[:240]
    digest = _sha256_text(text)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE native_resume_sources SET active=0,updated_at=CURRENT_TIMESTAMP "
            "WHERE tenant_id=? AND user_id=? AND active=1",
            (owner.tenant_id, owner.user_id),
        )
        prior = connection.execute(
            "SELECT source_id FROM native_resume_sources "
            "WHERE tenant_id=? AND user_id=? AND content_sha256=?",
            (owner.tenant_id, owner.user_id, digest),
        ).fetchone()
        if prior:
            source_id = str(prior["source_id"])
            connection.execute(
                "UPDATE native_resume_sources SET active=1,label=?,source_kind=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE source_id=? AND tenant_id=? AND user_id=?",
                (display, kind, source_id, owner.tenant_id, owner.user_id),
            )
        else:
            source_id = f"resume-source-{uuid4()}"
            connection.execute(
                """INSERT INTO native_resume_sources(
                    source_id,tenant_id,user_id,label,source_kind,content_sha256,content_text,active
                ) VALUES (?,?,?,?,?,?,?,1)""",
                (source_id, owner.tenant_id, owner.user_id, display, kind, digest, text),
            )
        connection.commit()
        return active_source(connection=connection)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def active_source(*, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT source_id,label,source_kind,content_sha256,content_text,created_at,updated_at
               FROM native_resume_sources
               WHERE tenant_id=? AND user_id=? AND active=1
               ORDER BY updated_at DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        if owns:
            connection.close()


def extract_docx_text(data: bytes) -> str:
    if not data:
        raise ValueError("Uploaded DOCX is empty.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            raw = archive.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as error:
        raise ValueError("The uploaded file is not a readable DOCX document.") from error
    root = ElementTree.fromstring(raw)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        text = "".join(node.text or "" for node in paragraph.iter(namespace + "t")).strip()
        if text:
            paragraphs.append(text)
    result = "\n".join(paragraphs).strip()
    return _clean_text(result, maximum=_MAX_SOURCE_CHARS, label="DOCX resume text")


def extract_uploaded_source(filename: str, data: bytes) -> tuple[str, str]:
    suffix = Path(str(filename or "")).suffix.casefold()
    if suffix in {".txt", ".md"}:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        return _clean_text(text, maximum=_MAX_SOURCE_CHARS, label="Uploaded resume text"), "text_upload"
    if suffix == ".docx":
        return extract_docx_text(data), "docx_upload"
    raise ValueError("Resume Studio V1 accepts .txt, .md, or .docx source files. PDF import is not enabled yet.")


def _contains_sensitive_token(value: str) -> bool:
    lower = str(value or "").casefold()
    return any(re.search(rf"\b{re.escape(token)}\b", lower) for token in _SENSITIVE_TOKENS)


def _segment_source(source_id: str, text: str) -> list[dict[str, str]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n|\n", text) if part.strip()]
    result: list[dict[str, str]] = []
    sequence = 0
    for paragraph in paragraphs:
        if _contains_sensitive_token(paragraph):
            continue
        words = paragraph.split()
        chunks: list[str] = []
        current: list[str] = []
        current_chars = 0
        for word in words:
            if current and current_chars + len(word) + 1 > _MAX_EVIDENCE_TEXT:
                chunks.append(" ".join(current))
                current, current_chars = [], 0
            current.append(word)
            current_chars += len(word) + 1
        if current:
            chunks.append(" ".join(current))
        for chunk in chunks:
            sequence += 1
            result.append({
                "evidence_id": f"source:{source_id}:{sequence}",
                "kind": "confirmed_resume_source",
                "label": f"Confirmed resume source segment {sequence}",
                "text": chunk,
                "source_reference": f"candidate://resume-source/{source_id}#{sequence}",
            })
            if len(result) >= _MAX_EVIDENCE_ITEMS:
                return result
    return result


def _legacy_profile_evidence(connection: sqlite3.Connection) -> list[dict[str, str]]:
    tables = {str(row[0]) for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "candidate_profile_facts" not in tables:
        return []
    rows = connection.execute(
        "SELECT fact_key,value_json,source_label FROM candidate_profile_facts ORDER BY fact_key"
    ).fetchall()
    result: list[dict[str, str]] = []
    for row in rows:
        key = str(row["fact_key"] or "")
        if _contains_sensitive_token(key):
            continue
        try:
            value = json.loads(str(row["value_json"] or "null"))
        except json.JSONDecodeError:
            value = str(row["value_json"] or "")
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        text = " ".join(str(text or "").split())
        if not text or _contains_sensitive_token(text):
            continue
        evidence_id = "profile:" + hashlib.sha256(f"{key}\0{text}".encode("utf-8")).hexdigest()[:24]
        result.append({
            "evidence_id": evidence_id,
            "kind": "candidate_profile_fact",
            "label": key,
            "text": text[:_MAX_EVIDENCE_TEXT],
            "source_reference": f"candidate://profile/{key}",
        })
    return result


def _digital_twin_evidence(connection: sqlite3.Connection) -> list[dict[str, str]]:
    try:
        from app.candidate_digital_twin import ensure_schema as ensure_twin_schema
        ensure_twin_schema(connection)
    except Exception:
        return []
    owner = current_owner(connection)
    rows = connection.execute(
        """SELECT f.fact_key,f.value_json,e.evidence_id,e.excerpt,e.source_reference
           FROM candidate_digital_twin_facts f
           JOIN candidate_digital_twin_evidence e
             ON e.fact_id=f.fact_id AND e.tenant_id=f.tenant_id AND e.user_id=f.user_id
           WHERE f.tenant_id=? AND f.user_id=? AND f.user_confirmed=1
           ORDER BY f.fact_key,e.created_at""",
        (owner.tenant_id, owner.user_id),
    ).fetchall()
    result: list[dict[str, str]] = []
    for row in rows:
        key = str(row["fact_key"] or "")
        if _contains_sensitive_token(key):
            continue
        excerpt = " ".join(str(row["excerpt"] or "").split())
        raw_value = str(row["value_json"] or "")
        try:
            value = json.loads(raw_value)
            value_text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            value_text = raw_value
        text = " | ".join(part for part in (str(value_text).strip(), excerpt) if part)
        if not text or _contains_sensitive_token(text):
            continue
        result.append({
            "evidence_id": f"twin:{row['evidence_id']}",
            "kind": "digital_twin_evidence",
            "label": key,
            "text": text[:_MAX_EVIDENCE_TEXT],
            "source_reference": str(row["source_reference"] or f"candidate://digital-twin/{key}"),
        })
    return result


def build_evidence_bundle(*, source_id: str | None = None) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if source_id:
            row = connection.execute(
                "SELECT * FROM native_resume_sources WHERE source_id=? AND tenant_id=? AND user_id=?",
                (source_id, owner.tenant_id, owner.user_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM native_resume_sources WHERE tenant_id=? AND user_id=? AND active=1 "
                "ORDER BY updated_at DESC LIMIT 1",
                (owner.tenant_id, owner.user_id),
            ).fetchone()
        if row is None:
            raise LookupError("Save a candidate-confirmed resume source before generating a native resume.")
        source = dict(row)
        items = _segment_source(str(source["source_id"]), str(source["content_text"]))
        seen = {item["evidence_id"] for item in items}
        for item in [*_digital_twin_evidence(connection), *_legacy_profile_evidence(connection)]:
            if item["evidence_id"] not in seen and len(items) < _MAX_EVIDENCE_ITEMS:
                items.append(item)
                seen.add(item["evidence_id"])
        if not items:
            raise LookupError("No non-sensitive candidate evidence is available for resume generation.")
        canonical = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return {
            "schema_version": "native-resume-evidence-v1",
            "tenant_id": owner.tenant_id,
            "user_id": owner.user_id,
            "source_id": str(source["source_id"]),
            "source_label": str(source["label"]),
            "source_sha256": str(source["content_sha256"]),
            "evidence_digest": _sha256_text(canonical),
            "items": items,
        }
    finally:
        connection.close()


_JOB_COLUMNS = (
    "id", "company_name", "title", "location_raw", "source", "description_raw",
    "responsibilities", "qualifications", "preferred_qualifications",
    "preferred_skills", "skills_keywords", "employment_type", "remote_type",
    "salary_raw", "target_track", "hunter_score",
)


def job_context(job_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        columns = ",".join(_JOB_COLUMNS)
        row = connection.execute(f"SELECT {columns} FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None:
            raise LookupError("Stored job not found.")
        return dict(row)
    finally:
        connection.close()


def resume_job_options(limit: int = 250) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT id,company_name,title,location_raw,source,hunter_score,first_seen_at
               FROM jobs
               WHERE trim(COALESCE(description_raw,'')) != ''
               ORDER BY COALESCE(hunter_score,-1) DESC, first_seen_at DESC
               LIMIT ?""",
            (max(1, min(int(limit), 1000)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def _job_text(context: dict[str, Any]) -> str:
    parts = []
    for key in (
        "title", "description_raw", "responsibilities", "qualifications",
        "preferred_qualifications", "preferred_skills", "skills_keywords",
    ):
        value = str(context.get(key) or "").strip()
        if value:
            parts.append(value)
    return "\n".join(parts)


def _jd_terms(context: dict[str, Any]) -> list[str]:
    text = _job_text(context)
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for match in _WORD_RE.finditer(text):
        raw = match.group(0).strip(".")
        key = raw.casefold()
        if key in _STOP_TERMS or len(key) < 3 or key.isdigit():
            continue
        counts[key] += 1
        display.setdefault(key, raw)
    explicit = str(context.get("skills_keywords") or "")
    for match in _WORD_RE.finditer(explicit):
        raw = match.group(0).strip(".")
        key = raw.casefold()
        if key not in _STOP_TERMS and len(key) >= 3:
            counts[key] += 3
            display.setdefault(key, raw)
    ordered = sorted(counts, key=lambda key: (-counts[key], key))
    return [display[key] for key in ordered[:120]]


def resume_plain_text(document: ResumeDocument) -> str:
    values: list[str] = [document.candidate_name, *document.contact.model_dump().values(), document.summary.text]
    for item in document.education:
        values.extend([item.institution, item.degree, item.dates, item.location, item.gpa])
    for group in document.skills:
        values.extend([group.label, *group.skills])
    for item in document.experience:
        values.extend([item.organization, item.title, item.dates, item.location])
        values.extend(b.text for b in item.bullets)
    for item in document.projects:
        values.extend([item.name, item.subtitle])
        values.extend(b.text for b in item.bullets)
    for item in document.certifications:
        values.extend([item.name, item.issuer])
    return "\n".join(str(value) for value in values if str(value or "").strip())


def _normalized_tokens(value: str) -> set[str]:
    return {token.casefold().strip(".") for token in _TOKEN_RE.findall(str(value or "")) if len(token) >= 2}


def _numbers(value: str) -> set[str]:
    return {match.group(0).replace(",", "") for match in _NUMBER_RE.finditer(str(value or ""))}


def _evidence_map(bundle: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {str(item["evidence_id"]): item for item in bundle["items"]}


def _support_text(ids: list[str], evidence: dict[str, dict[str, str]]) -> str:
    return "\n".join(evidence[item]["text"] for item in ids if item in evidence)


def _assert_numbers_supported(text: str, ids: list[str], evidence: dict[str, dict[str, str]], label: str) -> None:
    claim_numbers = _numbers(text)
    if not claim_numbers:
        return
    source_numbers = _numbers(_support_text(ids, evidence))
    unsupported = sorted(claim_numbers - source_numbers)
    if unsupported:
        raise ValueError(f"{label} contains unsupported numeric claim(s): {', '.join(unsupported)}")


def _assert_global_field_supported(text: str, evidence_text: str, label: str, minimum_overlap: float = 0.55) -> None:
    clean = " ".join(str(text or "").split())
    if not clean:
        return
    if _numbers(clean) - _numbers(evidence_text):
        raise ValueError(f"{label} contains a number not present in candidate evidence.")
    field_tokens = {token for token in _normalized_tokens(clean) if token not in _STOP_TERMS}
    if not field_tokens:
        return
    evidence_tokens = _normalized_tokens(evidence_text)
    overlap = len(field_tokens & evidence_tokens) / max(1, len(field_tokens))
    if overlap < minimum_overlap:
        raise ValueError(f"{label} is not sufficiently grounded in candidate evidence.")


def validate_document_evidence(document: ResumeDocument, bundle: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence_map(bundle)
    used = resume_evidence_ids(document)
    unknown = sorted(used - set(evidence))
    if unknown:
        raise ValueError("Resume references unknown evidence: " + ", ".join(unknown[:10]))
    if not used:
        raise ValueError("Resume contains no evidence references.")

    _assert_numbers_supported(document.summary.text, document.summary.evidence_ids, evidence, "Summary")
    for index, item in enumerate(document.education):
        combined = " ".join([item.institution, item.degree, item.dates, item.location, item.gpa])
        _assert_numbers_supported(combined, item.evidence_ids, evidence, f"Education {index + 1}")
    for index, group in enumerate(document.skills):
        _assert_numbers_supported(" ".join(group.skills), group.evidence_ids, evidence, f"Skill group {index + 1}")
    for item_index, item in enumerate(document.experience):
        for bullet_index, bullet in enumerate(item.bullets):
            _assert_numbers_supported(
                bullet.text, bullet.evidence_ids, evidence,
                f"Experience {item_index + 1} bullet {bullet_index + 1}",
            )
    for item_index, item in enumerate(document.projects):
        for bullet_index, bullet in enumerate(item.bullets):
            _assert_numbers_supported(
                bullet.text, bullet.evidence_ids, evidence,
                f"Project {item_index + 1} bullet {bullet_index + 1}",
            )

    global_evidence = "\n".join(item["text"] for item in evidence.values())
    _assert_global_field_supported(document.candidate_name, global_evidence, "Candidate name", 0.75)
    for key, value in document.contact.model_dump().items():
        if value:
            _assert_global_field_supported(value, global_evidence, f"Contact {key}", 0.45)
    for index, item in enumerate(document.education):
        _assert_global_field_supported(item.institution, _support_text(item.evidence_ids, evidence), f"Education {index + 1} institution", 0.6)
        _assert_global_field_supported(item.degree, _support_text(item.evidence_ids, evidence), f"Education {index + 1} degree", 0.45)
    for index, item in enumerate(document.experience):
        _assert_global_field_supported(item.organization, global_evidence, f"Experience {index + 1} organization", 0.6)
        _assert_global_field_supported(item.title, global_evidence, f"Experience {index + 1} title", 0.45)
        if item.dates:
            _assert_global_field_supported(item.dates, global_evidence, f"Experience {index + 1} dates", 0.35)
    for index, item in enumerate(document.projects):
        _assert_global_field_supported(item.name, global_evidence, f"Project {index + 1} name", 0.4)
    for index, item in enumerate(document.certifications):
        _assert_global_field_supported(item.name, _support_text(item.evidence_ids, evidence), f"Certification {index + 1}", 0.4)

    return {
        "status": "PASS",
        "evidence_ids_used": len(used),
        "evidence_ids_available": len(evidence),
        "unknown_evidence_ids": [],
        "numeric_claim_guard": "PASS",
        "sensitive_self_id_excluded": True,
    }


def analyze_document(document: ResumeDocument, context: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    issues = ats_readiness_issues(document)
    terms = _jd_terms(context)
    resume_tokens = _normalized_tokens(resume_plain_text(document))
    matched = [term for term in terms if term.casefold() in resume_tokens]
    missing = [term for term in terms if term.casefold() not in resume_tokens]
    coverage = round((len(matched) / len(terms) * 100), 1) if terms else 0.0
    evidence_audit = validate_document_evidence(document, bundle)

    structure = 0
    structure += 5 if document.summary.text else 0
    structure += 5 if document.skills else 0
    structure += 10 if document.experience else 0
    structure += 5 if document.education else 0
    structure += 5 if (document.projects or document.certifications) else 0
    jd_component = round(30 * coverage / 100)
    evidence_component = 25
    format_component = 15 if not issues else 8 if len(issues) == 1 else 0
    score = max(0, min(100, structure + jd_component + evidence_component + format_component))
    return {
        "schema_version": "native-resume-diagnostics-v1",
        "ats_readiness_estimate": score,
        "ats_score_label": "MUNSHI ATS readiness estimate",
        "word_count": document_word_count(document),
        "content_budget_issues": issues,
        "format_audit": "PASS" if not issues else "REVIEW",
        "truth_audit": evidence_audit,
        "jd_term_coverage_percent": coverage,
        "matched_jd_terms": matched[:60],
        "missing_jd_terms": missing[:40],
        "jd_terms_considered": len(terms),
        "one_page_render": "NOT_CHECKED",
        "source_sha256": bundle["source_sha256"],
        "evidence_digest": bundle["evidence_digest"],
    }


def _system_prompt() -> str:
    return """You are the controlled writing engine inside MUNSHI Resume Studio.
Return only a resume document that matches the supplied JSON schema.

Hard rules:
1. Use only facts supported by supplied evidence records.
2. Every summary, bullet, education item, skill group, and certification must cite one or more supplied evidence_id values.
3. Never invent employers, titles, dates, degrees, skills, metrics, numbers, locations, certifications, tools, or outcomes.
4. Preserve numeric values exactly from evidence. Do not calculate or infer new percentages or quantities.
5. Do not use sensitive self-identification data. It has already been excluded and must not be inferred.
6. Do not use em dashes.
7. Keep the resume ATS-safe, single-column, concise, and designed for one Letter page.
8. Summary <= 70 words. Bullets should usually be 18-32 words and never exceed 42.
9. Optimize wording and ordering for the job description without adding unsupported claims.
10. Copy candidate identity, organizations, education, dates, and contact details faithfully from evidence.
11. Skills may be included only when supported by evidence.
12. Do not include evidence IDs in visible text; put them only in evidence_ids fields.
13. Do not mention ATS, GPT, MUNSHI, evidence, or scoring in the visible resume.
14. If the job asks for a skill absent from evidence, omit it rather than fabricating it.
"""


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    text = "\n".join(parts).strip()
    if not text:
        raise RuntimeError("OpenAI returned no resume document text.")
    return text


def _json_from_model_text(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError("The model response was not valid JSON.") from error
        try:
            payload = json.loads(value[start:end + 1])
        except json.JSONDecodeError as nested:
            raise RuntimeError("The model response was not valid resume JSON.") from nested
    if not isinstance(payload, dict):
        raise RuntimeError("The model response must be a JSON object.")
    return payload


def _call_openai(*, prompt_payload: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    key = str(os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError("OpenAI is not configured for Resume Studio on this server.")
    model = str(os.getenv(MODEL_ENV) or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    schema = ResumeDocument.model_json_schema()
    body = {
        "model": model,
        "reasoning": {"effort": "medium"},
        "max_output_tokens": 10000,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _system_prompt()}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt_payload, ensure_ascii=False)}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "munshi_native_resume_v1",
                "strict": True,
                "schema": schema,
            }
        },
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        raise RuntimeError(f"OpenAI resume generation failed with HTTP {status}.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("OpenAI resume generation could not be completed.") from error
    document_payload = _json_from_model_text(_response_text(data))
    response_id = str(data.get("id") or "")[:200]
    return document_payload, response_id, model


_LOCKABLE = frozenset({"contact", "education", "skills", "experience", "projects", "certifications", "summary"})


def _apply_locks(
    proposed: ResumeDocument,
    parent: ResumeDocument | None,
    locked_sections: list[str],
) -> ResumeDocument:
    if parent is None or not locked_sections:
        return proposed
    payload = proposed.model_dump()
    original = parent.model_dump()
    for section in locked_sections:
        if section in _LOCKABLE:
            payload[section] = original[section]
    payload["candidate_name"] = original["candidate_name"]
    return ResumeDocument.model_validate(payload)


def _owner_version(connection: sqlite3.Connection, version_id: str) -> dict[str, Any]:
    owner = current_owner(connection)
    row = connection.execute(
        "SELECT * FROM native_resume_versions WHERE version_id=? AND tenant_id=? AND user_id=?",
        (str(version_id), owner.tenant_id, owner.user_id),
    ).fetchone()
    if row is None:
        raise LookupError("Resume version does not belong to the current candidate.")
    result = dict(row)
    result["document"] = json.loads(result.pop("document_json"))
    result["diagnostics"] = json.loads(result.pop("diagnostics_json"))
    result["locked_sections"] = json.loads(result.pop("locked_sections_json"))
    return result


def get_version(version_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        return _owner_version(connection, version_id)
    finally:
        connection.close()


def list_versions(*, job_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        params: list[Any] = [owner.tenant_id, owner.user_id]
        where = "tenant_id=? AND user_id=?"
        if job_id is not None:
            where += " AND job_id=?"
            params.append(int(job_id))
        params.append(max(1, min(int(limit), 500)))
        rows = connection.execute(
            f"""SELECT version_id,job_id,source_id,parent_version_id,version_number,instruction,
                       model_name,model_response_id,diagnostics_json,status,created_at
                FROM native_resume_versions WHERE {where}
                ORDER BY created_at DESC LIMIT ?""",
            tuple(params),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["diagnostics"] = json.loads(item.pop("diagnostics_json"))
            result.append(item)
        return result
    finally:
        connection.close()


def _next_version_number(connection: sqlite3.Connection, tenant_id: str, user_id: str, job_id: int) -> int:
    row = connection.execute(
        "SELECT COALESCE(MAX(version_number),0)+1 FROM native_resume_versions "
        "WHERE tenant_id=? AND user_id=? AND job_id=?",
        (tenant_id, user_id, int(job_id)),
    ).fetchone()
    return int(row[0])


def generate_resume(
    *,
    job_id: int,
    instruction: str = "",
    parent_version_id: str | None = None,
    locked_sections: list[str] | None = None,
) -> dict[str, Any]:
    context = job_context(int(job_id))
    bundle = build_evidence_bundle()
    instruction_text = str(instruction or "").strip()
    if len(instruction_text) > _MAX_INSTRUCTION_CHARS:
        raise ValueError(f"Revision instruction must be at most {_MAX_INSTRUCTION_CHARS:,} characters.")
    locks = sorted({str(value).strip().casefold() for value in (locked_sections or []) if str(value).strip()})
    invalid_locks = [value for value in locks if value not in _LOCKABLE]
    if invalid_locks:
        raise ValueError("Unsupported locked resume section.")

    parent_record: dict[str, Any] | None = None
    parent_document: ResumeDocument | None = None
    if parent_version_id:
        parent_record = get_version(parent_version_id)
        if int(parent_record["job_id"]) != int(job_id):
            raise ValueError("A revision must remain attached to the same job.")
        parent_document = ResumeDocument.model_validate(parent_record["document"])

    prompt_payload: dict[str, Any] = {
        "task": "revise_resume" if parent_document else "generate_resume",
        "job": context,
        "evidence_bundle": {
            "source_id": bundle["source_id"],
            "source_label": bundle["source_label"],
            "evidence_digest": bundle["evidence_digest"],
            "items": bundle["items"],
        },
        "instruction": instruction_text or (
            "Create the strongest truthful ATS-friendly one-page resume for this job. "
            "Prioritize the most relevant supported evidence."
        ),
        "locked_sections": locks,
        "current_resume": parent_document.model_dump() if parent_document else None,
    }

    candidate_payload, response_id, model = _call_openai(prompt_payload=prompt_payload)
    proposed = ResumeDocument.model_validate(candidate_payload)
    proposed = _apply_locks(proposed, parent_document, locks)
    diagnostics = analyze_document(proposed, context, bundle)

    if diagnostics["content_budget_issues"]:
        repair_payload = dict(prompt_payload)
        repair_payload["task"] = "repair_resume_content_budget"
        repair_payload["current_resume"] = proposed.model_dump()
        repair_payload["instruction"] = (
            "Repair only the listed content-budget issues while preserving all facts and evidence references. "
            "Do not add new facts. Issues: " + ", ".join(diagnostics["content_budget_issues"])
        )
        candidate_payload, repair_response_id, model = _call_openai(prompt_payload=repair_payload)
        proposed = ResumeDocument.model_validate(candidate_payload)
        proposed = _apply_locks(proposed, parent_document, locks)
        diagnostics = analyze_document(proposed, context, bundle)
        if diagnostics["content_budget_issues"]:
            raise ValueError("Generated resume still exceeds the ATS one-page content budget after one repair pass.")
        response_id = repair_response_id or response_id

    html = render_ats_html(proposed)
    html_digest = _sha256_text(html)
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        connection.execute("BEGIN IMMEDIATE")
        version_number = _next_version_number(connection, owner.tenant_id, owner.user_id, int(job_id))
        version_id = f"native-resume-{uuid4()}"
        connection.execute(
            """INSERT INTO native_resume_versions(
                version_id,tenant_id,user_id,job_id,source_id,parent_version_id,version_number,
                instruction,locked_sections_json,model_name,model_response_id,evidence_digest,
                document_json,diagnostics_json,html_sha256,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'VALIDATED')""",
            (
                version_id, owner.tenant_id, owner.user_id, int(job_id), bundle["source_id"],
                parent_version_id, version_number, instruction_text, json.dumps(locks),
                model, response_id or None, bundle["evidence_digest"],
                json.dumps(proposed.model_dump(), ensure_ascii=False, sort_keys=True),
                json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
                html_digest,
            ),
        )
        connection.commit()
        return _owner_version(connection, version_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def version_html(version_id: str) -> str:
    record = get_version(version_id)
    return render_ats_html(ResumeDocument.model_validate(record["document"]))


def version_diff(version_id: str) -> str:
    record = get_version(version_id)
    parent_id = record.get("parent_version_id")
    if not parent_id:
        return "This is the first version for this revision path."
    parent = get_version(str(parent_id))
    before = resume_plain_text(ResumeDocument.model_validate(parent["document"])).splitlines()
    after = resume_plain_text(ResumeDocument.model_validate(record["document"])).splitlines()
    return "\n".join(difflib.unified_diff(
        before, after,
        fromfile=f"v{parent['version_number']}",
        tofile=f"v{record['version_number']}",
        lineterm="",
    )) or "No visible text changed."


def _pdf_page_count(data: bytes) -> int:
    return max(1, len(re.findall(rb"/Type\s*/Page\b", data)))


def render_pdf_bytes(document: ResumeDocument) -> tuple[bytes, int]:
    chromium = next(
        (path for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
         if (path := shutil.which(name))),
        None,
    )
    if not chromium:
        raise RuntimeError("Chromium PDF renderer is not installed on this runtime.")
    html = render_ats_html(document)
    with tempfile.TemporaryDirectory(prefix="munshi-resume-") as directory:
        root = Path(directory)
        html_path = root / "resume.html"
        pdf_path = root / "resume.pdf"
        html_path.write_text(html, encoding="utf-8")
        command = [
            chromium, "--headless", "--no-sandbox", "--disable-gpu",
            "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
            html_path.resolve().as_uri(),
        ]
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45, check=False)
        if completed.returncode != 0 or not pdf_path.exists():
            raise RuntimeError("Chromium could not render the resume PDF.")
        data = pdf_path.read_bytes()
    if not data.startswith(b"%PDF"):
        raise RuntimeError("Resume PDF renderer returned an invalid document.")
    return data, _pdf_page_count(data)


def version_pdf(version_id: str) -> tuple[bytes, int]:
    record = get_version(version_id)
    return render_pdf_bytes(ResumeDocument.model_validate(record["document"]))


def _docx_paragraph(text: str, *, bold: bool = False, style: str | None = None) -> str:
    style_xml = f'<w:pPr><w:pStyle w:val="{xml_escape(style)}"/></w:pPr>' if style else ""
    run_props = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return (
        "<w:p>" + style_xml + "<w:r>" + run_props +
        f'<w:t xml:space="preserve">{xml_escape(str(text))}</w:t></w:r></w:p>'
    )


def _docx_bullet(text: str) -> str:
    return _docx_paragraph("• " + str(text), style="Body")


def render_docx_bytes(document: ResumeDocument) -> bytes:
    body: list[str] = [
        _docx_paragraph(document.candidate_name, bold=True, style="Name"),
        _docx_paragraph(" | ".join(value for value in document.contact.model_dump().values() if value), style="Contact"),
        _docx_paragraph("PROFESSIONAL SUMMARY", bold=True, style="Heading"),
        _docx_paragraph(document.summary.text, style="Body"),
    ]
    if document.education:
        body.append(_docx_paragraph("EDUCATION", bold=True, style="Heading"))
        for item in document.education:
            line = " | ".join(value for value in (item.institution, item.dates) if value)
            body.append(_docx_paragraph(line, bold=True, style="Body"))
            body.append(_docx_paragraph(" | ".join(value for value in (item.degree, item.location, item.gpa) if value), style="Body"))
    if document.skills:
        body.append(_docx_paragraph("SKILLS", bold=True, style="Heading"))
        for group in document.skills:
            body.append(_docx_paragraph(f"{group.label}: {', '.join(group.skills)}", style="Body"))
    if document.experience:
        body.append(_docx_paragraph("WORK EXPERIENCE", bold=True, style="Heading"))
        for item in document.experience:
            body.append(_docx_paragraph(" | ".join(value for value in (item.organization, item.title, item.dates) if value), bold=True, style="Body"))
            if item.location:
                body.append(_docx_paragraph(item.location, style="Body"))
            body.extend(_docx_bullet(bullet.text) for bullet in item.bullets)
    if document.projects:
        body.append(_docx_paragraph("PROJECTS", bold=True, style="Heading"))
        for item in document.projects:
            body.append(_docx_paragraph(" | ".join(value for value in (item.name, item.subtitle) if value), bold=True, style="Body"))
            body.extend(_docx_bullet(bullet.text) for bullet in item.bullets)
    if document.certifications:
        body.append(_docx_paragraph("CERTIFICATIONS & ACHIEVEMENTS", bold=True, style="Heading"))
        for item in document.certifications:
            body.append(_docx_bullet(" - ".join(value for value in (item.name, item.issuer) if value)))

    document_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body>{''.join(body)}
<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="648" w:right="792" w:bottom="648" w:left="792"/></w:sectPr>
</w:body></w:document>'''
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Name"><w:name w:val="Name"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="40"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Contact"><w:name w:val="Contact"/><w:pPr><w:jc w:val="center"/><w:spacing w:after="80"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading"><w:name w:val="Heading"/><w:pPr><w:spacing w:before="100" w:after="30"/><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="000000"/></w:pBdr></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:b/><w:sz w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Body"><w:name w:val="Body"/><w:pPr><w:spacing w:after="25"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="21"/></w:rPr></w:style>
</w:styles>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''
    doc_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document_xml)
        archive.writestr("word/styles.xml", styles_xml)
        archive.writestr("word/_rels/document.xml.rels", doc_rels)
    return buffer.getvalue()


def version_docx(version_id: str) -> bytes:
    record = get_version(version_id)
    return render_docx_bytes(ResumeDocument.model_validate(record["document"]))


def safe_filename(record: dict[str, Any], context: dict[str, Any], extension: str) -> str:
    base = f"{context.get('company_name') or 'company'}_{context.get('title') or 'resume'}_v{record['version_number']}"
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:120] or "munshi_resume"
    return f"{cleaned}.{extension.lstrip('.')}"
