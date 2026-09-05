"""Native Resume Studio V3: PDF intake + structured candidate-profile extraction.

V3 is additive and preparation-only. It keeps the V2 writer/authority boundary,
adds local PDF text extraction, and can derive a reviewable structured profile
from the candidate-confirmed Master Resume. The extractor never infers voluntary
self-identification fields and never makes the extracted profile application
authority without explicit user confirmation.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, Field

from app import native_resume_service_v2 as v2
from app.secure_vault import read_secret, store_secret, vault_available

SCHEMA_VERSION = "native-resume-studio-service-v3"
PROFILE_SECRET_TYPE = "native_resume_profile_snapshot_v3"
_MAX_PROFILE_RESPONSE_TOKENS = 6000


class ContactProfile(BaseModel):
    full_name: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    portfolio: str = ""


class EducationProfile(BaseModel):
    institution: str = ""
    degree: str = ""
    field: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    gpa: str = ""
    details: list[str] = Field(default_factory=list)


class ExperienceProfile(BaseModel):
    employer: str = ""
    title: str = ""
    location: str = ""
    start_date: str = ""
    end_date: str = ""
    bullets: list[str] = Field(default_factory=list)


class ProjectProfile(BaseModel):
    name: str = ""
    description: str = ""
    tools: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class CertificationProfile(BaseModel):
    name: str = ""
    issuer: str = ""
    date: str = ""
    credential_id: str = ""


class SkillCategory(BaseModel):
    category: str = "Other"
    skills: list[str] = Field(default_factory=list)


class ApplicationDefaultsProfile(BaseModel):
    """Only resume-explicit defaults. Unknown values remain blank/None."""

    work_authorization_country: str = ""
    authorization_basis: str = ""
    visa_or_permit: str = ""
    sponsorship_required: bool | None = None
    willing_to_relocate: bool | None = None
    work_modes: list[str] = Field(default_factory=list)


class CandidateProfileExtract(BaseModel):
    professional_summary: str = ""
    contact: ContactProfile = Field(default_factory=ContactProfile)
    education: list[EducationProfile] = Field(default_factory=list)
    experience: list[ExperienceProfile] = Field(default_factory=list)
    projects: list[ProjectProfile] = Field(default_factory=list)
    skills: list[SkillCategory] = Field(default_factory=list)
    certifications: list[CertificationProfile] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    application_defaults: ApplicationDefaultsProfile = Field(default_factory=ApplicationDefaultsProfile)
    extraction_warnings: list[str] = Field(default_factory=list)


PROFILE_SCHEMA = """
CREATE TABLE IF NOT EXISTS native_resume_profile_extracts(
    extraction_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    profile_sha256 TEXT NOT NULL,
    profile_json TEXT,
    vault_label TEXT,
    storage_mode TEXT NOT NULL CHECK(storage_mode IN ('aes_gcm_vault','sqlite_plaintext')),
    model_name TEXT NOT NULL,
    model_response_id TEXT,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','CONFIRMED')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    confirmed_at TEXT,
    UNIQUE(tenant_id,user_id,source_id,profile_sha256),
    FOREIGN KEY(tenant_id,user_id)
        REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
    FOREIGN KEY(tenant_id,user_id,source_id)
        REFERENCES native_resume_sources(tenant_id,user_id,source_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_native_resume_profile_owner_source
ON native_resume_profile_extracts(tenant_id,user_id,source_id,created_at DESC);
"""


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or v2.v1.get_connection()
    try:
        v2.ensure_schema(connection)
        connection.executescript(PROFILE_SCHEMA)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def extract_pdf_text(data: bytes) -> str:
    """Extract selectable text locally from a PDF. No OCR or network call occurs."""
    if not data:
        raise ValueError("Uploaded PDF is empty.")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as error:
        raise ValueError("The uploaded file is not a readable PDF document.") from error
    if getattr(reader, "is_encrypted", False):
        try:
            unlocked = reader.decrypt("")
        except Exception as error:
            raise ValueError("Password-protected PDFs are not supported yet. Upload an unlocked PDF or DOCX.") from error
        if not unlocked:
            raise ValueError("Password-protected PDFs are not supported yet. Upload an unlocked PDF or DOCX.")
    pages: list[str] = []
    try:
        for page in reader.pages:
            text = str(page.extract_text() or "").strip()
            if text:
                pages.append(text)
    except Exception as error:
        raise ValueError("MUNSHI could not extract text from this PDF.") from error
    if not pages:
        raise ValueError("This PDF contains no extractable text. If it is a scanned image, export it as a text PDF or DOCX first.")
    return v2.v1._clean_text("\n\n".join(pages), maximum=v2.v1._MAX_SOURCE_CHARS, label="PDF resume text")


def extract_uploaded_source(filename: str, data: bytes) -> tuple[str, str]:
    suffix = str(filename or "").strip().casefold()
    if suffix.endswith(".pdf"):
        # V1's source-kind CHECK predates PDF. Store the extracted text through
        # the compatible text-upload kind while preserving the original filename
        # as the human-visible label.
        return extract_pdf_text(data), "text_upload"
    return v2.extract_uploaded_source(filename, data)


def _profile_system_prompt() -> str:
    return (
        "You are MUNSHI's candidate-profile extraction engine. Convert only the supplied, candidate-confirmed Master Resume into the requested JSON schema. "
        "Do not invent, infer, embellish, normalize upward, or guess employers, titles, dates, locations, degrees, GPAs, metrics, tools, skills, certifications, work authorization, sponsorship, or preferences. "
        "A concise professional_summary may paraphrase the resume but must stay strictly grounded in it. "
        "If a field is not explicit, return an empty string, empty list, or null as appropriate. "
        "Never infer or emit gender, race, ethnicity, religion, disability, veteran status, age, marital status, citizenship, or other voluntary self-identification from names, schools, locations, language, or any other proxy. "
        "Work authorization and sponsorship defaults may be populated only if explicitly stated in the supplied resume text. "
        "Preserve exact numeric claims and dates as written. Return JSON only through the provided schema."
    )


def _response_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise RuntimeError("OpenAI returned no structured profile content.")


def _json_from_text(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise RuntimeError("OpenAI returned invalid structured profile JSON.") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("OpenAI returned an invalid profile object.")
    return parsed


def _profile_secret_label(owner: Any, extraction_id: str) -> str:
    return f"{owner.tenant_id}:{owner.user_id}:resume-profile:{extraction_id}"


def _persist_profile(*, source: dict[str, Any], profile: CandidateProfileExtract, model: str, response_id: str) -> dict[str, Any]:
    payload = json.dumps(profile.model_dump(), ensure_ascii=False, sort_keys=True)
    payload_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    extraction_id = f"profile-extract-{uuid4()}"
    connection = v2.v1.get_connection()
    vault_label: str | None = None
    storage_mode = "sqlite_plaintext"
    profile_json: str | None = payload
    try:
        ensure_schema(connection)
        owner = v2.v1.current_owner(connection)
        if vault_available():
            vault_label = _profile_secret_label(owner, extraction_id)
            store_secret(PROFILE_SECRET_TYPE, payload, account_label=vault_label)
            profile_json = None
            storage_mode = "aes_gcm_vault"
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO native_resume_profile_extracts(
                extraction_id,tenant_id,user_id,source_id,source_sha256,profile_sha256,
                profile_json,vault_label,storage_mode,model_name,model_response_id,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'DRAFT')""",
            (
                extraction_id, owner.tenant_id, owner.user_id, str(source["source_id"]),
                str(source["content_sha256"]), payload_sha, profile_json, vault_label,
                storage_mode, model, response_id or None,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_profile_extract(extraction_id)


def extract_profile_from_source(source: dict[str, Any] | None = None) -> dict[str, Any]:
    source = source or v2.active_source()
    if not source or not str(source.get("content_text") or "").strip():
        raise ValueError("Save a confirmed Master Resume source before extracting a profile.")
    config = v2._resolve_writer_config()
    body = {
        "model": config["model"],
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": min(int(config["max_output_tokens"]), _MAX_PROFILE_RESPONSE_TOKENS),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _profile_system_prompt()}]},
            {"role": "user", "content": [{"type": "input_text", "text": str(source["content_text"])}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "munshi_candidate_profile_extract_v3",
                "strict": False,
                "schema": CandidateProfileExtract.model_json_schema(),
            }
        },
    }
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"OpenAI profile extraction failed with HTTP {error.response.status_code}.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("OpenAI profile extraction could not be completed.") from error
    profile = CandidateProfileExtract.model_validate(_json_from_text(_response_text(payload)))
    return _persist_profile(
        source=source,
        profile=profile,
        model=str(config["model"]),
        response_id=str(payload.get("id") or "")[:200],
    )


def _decode_profile_row(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    if data.get("storage_mode") == "aes_gcm_vault":
        label = str(data.get("vault_label") or "")
        if not label:
            raise RuntimeError("Encrypted profile metadata is incomplete.")
        payload = read_secret(PROFILE_SECRET_TYPE, account_label=label)
        if not payload:
            raise RuntimeError("Encrypted profile payload is unavailable.")
    else:
        payload = str(data.get("profile_json") or "")
    profile = CandidateProfileExtract.model_validate_json(payload)
    data["profile"] = profile.model_dump()
    data.pop("profile_json", None)
    data.pop("vault_label", None)
    return data


def get_profile_extract(extraction_id: str) -> dict[str, Any]:
    connection = v2.v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v2.v1.current_owner(connection)
        row = connection.execute(
            "SELECT * FROM native_resume_profile_extracts WHERE extraction_id=? AND tenant_id=? AND user_id=?",
            (str(extraction_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Profile extraction was not found for this candidate.")
        return _decode_profile_row(dict(row))
    finally:
        connection.close()


def latest_profile_for_source(source_id: str) -> dict[str, Any]:
    connection = v2.v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v2.v1.current_owner(connection)
        row = connection.execute(
            """SELECT * FROM native_resume_profile_extracts
               WHERE source_id=? AND tenant_id=? AND user_id=?
               ORDER BY created_at DESC LIMIT 1""",
            (str(source_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        return _decode_profile_row(dict(row)) if row else {}
    finally:
        connection.close()


def confirm_profile_extract(extraction_id: str) -> dict[str, Any]:
    connection = v2.v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v2.v1.current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT 1 FROM native_resume_profile_extracts WHERE extraction_id=? AND tenant_id=? AND user_id=?",
            (str(extraction_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("Profile extraction was not found for this candidate.")
        connection.execute(
            """UPDATE native_resume_profile_extracts
               SET status='CONFIRMED',confirmed_at=CURRENT_TIMESTAMP
               WHERE extraction_id=? AND tenant_id=? AND user_id=?""",
            (str(extraction_id), owner.tenant_id, owner.user_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return get_profile_extract(extraction_id)


# V2 writer/download helpers remain authoritative for this preparation-only layer.
generate_resume = v2.generate_resume
writer_status = v2.writer_status
save_personal_api_key = v2.save_personal_api_key
delete_personal_api_key = v2.delete_personal_api_key
save_writer_settings = v2.save_writer_settings
active_source = v2.active_source
build_evidence_bundle = v2.build_evidence_bundle
get_version = v2.get_version
job_context = v2.job_context
list_versions = v2.list_versions
native_resume_authority_enabled = v2.native_resume_authority_enabled
resume_job_options = v2.resume_job_options
safe_filename = v2.safe_filename
save_confirmed_source = v2.save_confirmed_source
version_diff = v2.version_diff
version_docx = v2.version_docx
version_html = v2.version_html
version_pdf = v2.version_pdf
