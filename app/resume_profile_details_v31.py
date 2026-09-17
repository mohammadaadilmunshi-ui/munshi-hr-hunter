"""Resume Studio V3.1 helpers: resilient PDF text reflow + encrypted candidate profile details.

This module repairs layout-fragmented PDF extraction before the resume is saved as
truth evidence and adds candidate-entered application/profile details that are
stored AES-GCM encrypted only. Sensitive fields are never passed into the LLM
profile extractor and never fall back to plaintext SQLite storage.
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app import native_resume_service_v3 as v3
from app.secure_vault import read_secret, store_secret, vault_available

PROFILE_DETAILS_SECRET_TYPE = "candidate_profile_details_v31"
PROFILE_DETAILS_ENVELOPE_SCHEMA = "candidate-profile-details-envelope-v1"

_SECTION_HEADINGS = {
    "summary",
    "professional summary",
    "profile",
    "objective",
    "education",
    "experience",
    "work experience",
    "professional experience",
    "employment",
    "projects",
    "project experience",
    "skills",
    "technical skills",
    "core skills",
    "certifications",
    "certificates",
    "licenses & certifications",
    "languages",
    "awards",
    "activities",
    "leadership",
}
_BULLET_PREFIX_RE = re.compile(r"^[\s\u2022\u25cf\u25aa\u25e6\u2043\-*]+")
_SPACE_RE = re.compile(r"[ \t]+")
_CONTACT_RE = re.compile(r"(@|https?://|www\.|linkedin\.com|github\.com)", re.I)
_SENTENCE_END_RE = re.compile(r"[.!?;:]$|\)$")


class CandidateProvidedProfileDetails(BaseModel):
    """Candidate-entered fields only. Never inferred from the resume or LLM."""

    open_to_work: bool | None = None

    work_authorization_country: str = ""
    authorization_basis: str = ""
    visa_or_permit: str = ""
    authorization_status: str = ""
    authorized_to_work: bool | None = None
    sponsorship_required: bool | None = None

    in_person_ok: bool | None = None
    willing_to_relocate: bool | None = None
    start_immediately: bool | None = None
    has_transport: bool | None = None
    needs_accommodations: bool | None = None
    work_modes: list[str] = Field(default_factory=list)

    prior_employee: bool | None = None
    government_clearance: bool | None = None
    government_ties: bool | None = None

    gender: str = ""
    ethnicity: str = ""
    veteran: bool | None = None
    disability: bool | None = None


class CandidateProfileDetailsEnvelope(BaseModel):
    """Backward-compatible encrypted revision envelope for candidate-entered truth."""

    schema_version: Literal[PROFILE_DETAILS_ENVELOPE_SCHEMA] = PROFILE_DETAILS_ENVELOPE_SCHEMA
    revision: int = Field(default=0, ge=0)
    updated_at: str = ""
    values: CandidateProvidedProfileDetails = Field(default_factory=CandidateProvidedProfileDetails)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_line(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\u200b", "")
    return _SPACE_RE.sub(" ", value).strip()


def _is_section_heading(line: str) -> bool:
    compact = line.strip().strip(":").casefold()
    if compact in _SECTION_HEADINGS:
        return True
    words = compact.split()
    # Generic all-caps headings are allowed only when they are long enough to
    # be real labels. Short resume tokens such as NJ, HR, BI, AI, GPA, etc.
    # must remain normal content and must never split the reconstructed text.
    return (
        bool(words)
        and len(words) <= 5
        and line.isupper()
        and 5 <= len(line.strip().strip(":")) <= 48
        and not line.rstrip().endswith((",", ".", ";"))
    )


def _is_bullet(line: str) -> bool:
    stripped = line.lstrip()
    return bool(stripped) and stripped[0] in "•●▪◦⁃-*"


def _looks_fragmented(lines: list[str]) -> bool:
    content = [line for line in lines if line]
    if len(content) < 8:
        return False
    very_short = sum(1 for line in content if len(line.split()) <= 2 and len(line) <= 28)
    single_word = sum(1 for line in content if len(line.split()) == 1 and len(line) <= 24)
    # The user's failing PDF produced essentially one token per visual line.
    return (very_short / len(content) >= 0.62) or (single_word / len(content) >= 0.45)


def normalize_pdf_text(raw_text: str) -> str:
    """Repair common PDF layout fragmentation while preserving resume sections/bullets."""
    value = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [_clean_line(line) for line in value.split("\n")]

    if not _looks_fragmented(lines):
        output: list[str] = []
        blank = False
        for line in lines:
            if not line:
                if output and not blank:
                    output.append("")
                blank = True
                continue
            output.append(line)
            blank = False
        return "\n".join(output).strip()

    output: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        nonlocal buffer
        if buffer:
            joined = " ".join(part for part in buffer if part).strip()
            if joined:
                output.append(joined)
            buffer = []

    for line in lines:
        if not line:
            # Empty lines from fragmented PDFs are often spacing artifacts, not
            # semantic paragraph breaks. Only flush once the buffer resembles a
            # completed sentence/record.
            if buffer:
                joined = " ".join(buffer)
                if len(joined) >= 80 or _SENTENCE_END_RE.search(joined):
                    flush_buffer()
            continue

        if _is_section_heading(line):
            flush_buffer()
            if output and output[-1] != "":
                output.append("")
            output.append(line.upper())
            output.append("")
            continue

        if _is_bullet(line):
            flush_buffer()
            bullet = _BULLET_PREFIX_RE.sub("", line).strip()
            output.append(f"- {bullet}" if bullet else "-")
            continue

        if _CONTACT_RE.search(line):
            flush_buffer()
            output.append(line)
            continue

        buffer.append(line)
        joined = " ".join(buffer)
        if len(joined) >= 160 and _SENTENCE_END_RE.search(line):
            flush_buffer()

    flush_buffer()

    # Remove duplicate blank lines introduced around headings.
    compact: list[str] = []
    for line in output:
        if line == "" and compact and compact[-1] == "":
            continue
        compact.append(line)
    return "\n".join(compact).strip()


def _page_text(page: Any) -> str:
    """Prefer coordinate-aware layout extraction, then fall back safely."""
    try:
        text = page.extract_text(extraction_mode="layout")
    except (TypeError, ValueError, NotImplementedError):
        text = page.extract_text()
    return str(text or "")


def extract_uploaded_source(filename: str, data: bytes) -> tuple[str, str]:
    suffix = str(filename or "").strip().casefold()
    if not suffix.endswith(".pdf"):
        return v3.v2.extract_uploaded_source(filename, data)

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
            normalized = normalize_pdf_text(_page_text(page))
            if normalized:
                pages.append(normalized)
    except Exception as error:
        raise ValueError("MUNSHI could not extract coherent text from this PDF.") from error

    if not pages:
        raise ValueError("This PDF contains no extractable text. If it is a scanned image, export it as a text PDF or DOCX first.")

    merged = "\n\n".join(pages)
    return (
        v3.v2.v1._clean_text(merged, maximum=v3.v2.v1._MAX_SOURCE_CHARS, label="PDF resume text"),
        "text_upload",
    )


def _owner_label() -> str:
    connection = v3.v2.v1.get_connection()
    try:
        v3.ensure_schema(connection)
        owner = v3.v2.v1.current_owner(connection)
        return f"{owner.tenant_id}:{owner.user_id}:candidate-profile-details:v31"
    finally:
        connection.close()


def candidate_profile_details_encryption_available() -> bool:
    return vault_available()


def load_candidate_profile_details_envelope() -> CandidateProfileDetailsEnvelope:
    """Load versioned candidate-entered truth, accepting legacy V3.1 payloads.

    Legacy secrets stored the values object directly. They are interpreted as
    revision 0 and are upgraded to the encrypted envelope on the next save.
    """
    if not vault_available():
        return CandidateProfileDetailsEnvelope()
    payload = read_secret(PROFILE_DETAILS_SECRET_TYPE, account_label=_owner_label())
    if not payload:
        return CandidateProfileDetailsEnvelope()
    try:
        raw = json.loads(payload)
        if isinstance(raw, dict) and raw.get("schema_version") == PROFILE_DETAILS_ENVELOPE_SCHEMA:
            return CandidateProfileDetailsEnvelope.model_validate(raw)
        legacy = CandidateProvidedProfileDetails.model_validate(raw)
        return CandidateProfileDetailsEnvelope(values=legacy)
    except Exception as error:
        raise RuntimeError("Encrypted candidate profile details could not be decoded.") from error


def load_candidate_profile_details() -> dict[str, Any]:
    return load_candidate_profile_details_envelope().values.model_dump()


def save_candidate_profile_details(values: dict[str, Any]) -> dict[str, Any]:
    if not vault_available():
        raise RuntimeError(
            "Encrypted candidate profile details require MUNSHI_VAULT_KEY. "
            "Sensitive self-ID fields are never saved to plaintext fallback storage."
        )
    model = CandidateProvidedProfileDetails.model_validate(values)
    previous = load_candidate_profile_details_envelope()
    envelope = CandidateProfileDetailsEnvelope(
        revision=previous.revision + 1,
        updated_at=_now(),
        values=model,
    )
    payload = json.dumps(envelope.model_dump(), ensure_ascii=False, sort_keys=True)
    store_secret(PROFILE_DETAILS_SECRET_TYPE, payload, account_label=_owner_label())
    return model.model_dump()
