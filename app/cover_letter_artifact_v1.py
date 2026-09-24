"""Immutable one-page cover-letter PDF bound to Hunter authority truth.

No LLM call, ATS action, email, browser, or submission authority exists here.
A trusted caller supplies final text; this module validates and binds the exact
artifact to current owner, preflight, job snapshot, Candidate Truth and Native
Resume V5 execution artifact.
"""

from __future__ import annotations

import hashlib
import html
import io
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app import application_execution_artifact
from app import application_preflight_package_v1 as preflight_v1
from app.database import get_connection
from app.phase67_common import safe_owned_job_snapshot
from app.tenant_foundation import current_owner
from app.tenant_foundation import ensure_schema as ensure_tenant_schema

ARTIFACT_VERSION = "munshi-cover-letter-artifact-v1"
MAX_CONTENT_CHARS = 12000
SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS cover_letter_artifacts_v1(
 cover_letter_id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,user_id TEXT NOT NULL,application_id TEXT NOT NULL,
 preflight_package_id TEXT NOT NULL,preflight_package_digest TEXT NOT NULL CHECK(length(preflight_package_digest)=64),
 job_id INTEGER NOT NULL,job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
 candidate_profile_digest TEXT NOT NULL CHECK(length(candidate_profile_digest)=64),resume_version_id TEXT NOT NULL,
 resume_artifact_sha256 TEXT NOT NULL CHECK(length(resume_artifact_sha256)=64),content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
 artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),filename TEXT NOT NULL,mime_type TEXT NOT NULL,
 byte_count INTEGER NOT NULL CHECK(byte_count>0),page_count INTEGER NOT NULL CHECK(page_count=1),artifact_reference TEXT NOT NULL,
 content_text TEXT NOT NULL,content BLOB NOT NULL,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(tenant_id,user_id,artifact_reference),UNIQUE(tenant_id,user_id,application_id,preflight_package_digest,job_snapshot_sha256,candidate_profile_digest,resume_artifact_sha256,content_sha256),
 FOREIGN KEY(tenant_id,user_id) REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT);""",
    "CREATE INDEX IF NOT EXISTS idx_cover_letter_artifacts_v1_owner_application ON cover_letter_artifacts_v1(tenant_id,user_id,application_id,created_at DESC);",
)


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for s in SCHEMA_STATEMENTS:
            connection.execute(s)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content(value: str) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if not text:
        raise ValueError("Cover letter content is required.")
    if len(text) > MAX_CONTENT_CHARS:
        raise ValueError(
            f"Cover letter content must be at most {MAX_CONTENT_CHARS:,} characters."
        )
    return text


def _safe_filename(company: str, title: str) -> str:
    base = f"{company or 'company'}_{title or 'role'}_cover_letter"
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")[:120]
    return f"{clean or 'munshi_cover_letter'}.pdf"


def _render_pdf(text: str, *, company: str, title: str) -> tuple[bytes, int]:
    chromium = next(
        (
            p
            for n in (
                "chromium",
                "chromium-browser",
                "google-chrome",
                "google-chrome-stable",
            )
            if (p := shutil.which(n))
        ),
        None,
    )
    if not chromium:
        raise RuntimeError("Chromium cover-letter PDF renderer is not installed.")
    paragraphs = [
        f"<p>{html.escape(p.strip())}</p>"
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    ]
    doc = (
        '<!doctype html><html><head><meta charset="utf-8"><style>@page{size:Letter;margin:.72in}html,body{margin:0;padding:0}body{font-family:Arial,Helvetica,sans-serif;font-size:10.7pt;line-height:1.42;color:#111}p{margin:0 0 11pt 0}</style></head><body>'
        + "".join(paragraphs)
        + "</body></html>"
    )
    with tempfile.TemporaryDirectory(prefix="munshi-cover-letter-") as d:
        root = Path(d)
        hp = root / "cover-letter.html"
        pp = root / "cover-letter.pdf"
        hp.write_text(doc, encoding="utf-8")
        c = subprocess.run(
            [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pp}",
                hp.resolve().as_uri(),
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
        if c.returncode != 0 or not pp.exists():
            raise RuntimeError("Chromium could not render the cover-letter PDF.")
        data = pp.read_bytes()
    try:
        pages = len(PdfReader(io.BytesIO(data), strict=True).pages)
    except Exception as e:
        raise RuntimeError(
            "Cover-letter PDF renderer returned an invalid document."
        ) from e
    if pages != 1:
        raise ValueError(
            f"Cover letter must render to exactly one page; parsed {pages} pages."
        )
    return data, pages


def _current_bindings(preflight_package_id: str) -> dict[str, Any]:
    package = preflight_v1.get_preflight_package(str(preflight_package_id))
    fresh = preflight_v1.preflight_package_freshness(str(preflight_package_id))
    if fresh.get("fresh") is not True:
        raise RuntimeError("Cover letter preflight package is stale.")
    if str(package.get("status")) != "READY_TO_APPLY":
        raise ValueError("Cover letter requires a READY_TO_APPLY preflight package.")
    snap = dict(package.get("snapshot") or {})
    truth = dict(snap.get("application_truth") or {})
    cand = dict(truth.get("candidate_profile_binding") or {})
    cd = str(cand.get("profile_digest") or "")
    if len(cd) != 64:
        raise ValueError("Cover letter Candidate Truth binding is incomplete.")
    c = get_connection()
    try:
        ensure_tenant_schema(c)
        owner = current_owner(c)
    finally:
        c.close()
    if (
        str(truth.get("tenant_id") or "") != owner.tenant_id
        or str(truth.get("user_id") or "") != owner.user_id
    ):
        raise PermissionError(
            "Cover letter preflight package owner binding is invalid."
        )
    job_id = int(package["job_id"])
    current = safe_owned_job_snapshot(job_id)
    current_jd = str(current["job_snapshot_sha256"])
    immutable_continuation = fresh.get("immutable_inflight_continuation") is True
    jd = (
        str(package["job_snapshot_sha256"])
        if immutable_continuation
        else current_jd
    )
    if current_jd != str(package["job_snapshot_sha256"]) and not immutable_continuation:
        raise RuntimeError("Cover letter job snapshot is stale.")
    rv = str(package["resume_version_id"])
    resume = application_execution_artifact.native(
        application_id=str(package["application_id"]), version_id=rv
    )
    rs = str(resume.get("artifact_sha256") or "")
    if len(rs) != 64:
        raise ValueError("Cover letter resume artifact binding is incomplete.")
    job = dict(snap.get("job") or {})
    return {
        "tenant_id": owner.tenant_id,
        "user_id": owner.user_id,
        "application_id": str(package["application_id"]),
        "preflight_package_id": str(preflight_package_id),
        "preflight_package_digest": str(package["package_digest"]),
        "job_id": job_id,
        "job_snapshot_sha256": jd,
        "candidate_profile_digest": cd,
        "resume_version_id": rv,
        "resume_artifact_sha256": rs,
        "company": str(job.get("company_name") or job.get("company") or ""),
        "title": str(job.get("title") or ""),
    }


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "cover_letter_id": str(row["cover_letter_id"]),
        "version": ARTIFACT_VERSION,
        "application_id": str(row["application_id"]),
        "preflight_package_id": str(row["preflight_package_id"]),
        "preflight_package_digest": str(row["preflight_package_digest"]),
        "job_id": int(row["job_id"]),
        "job_snapshot_sha256": str(row["job_snapshot_sha256"]),
        "candidate_profile_digest": str(row["candidate_profile_digest"]),
        "resume_version_id": str(row["resume_version_id"]),
        "resume_artifact_sha256": str(row["resume_artifact_sha256"]),
        "content_sha256": str(row["content_sha256"]),
        "artifact_id": str(row["cover_letter_id"]),
        "artifact_reference": str(row["artifact_reference"]),
        "artifact_sha256": str(row["artifact_sha256"]),
        "filename": str(row["filename"]),
        "mime_type": str(row["mime_type"]),
        "byte_count": int(row["byte_count"]),
        "page_count": int(row["page_count"]),
        "immutable": True,
        "submission_authority": False,
        "created_at": str(row["created_at"]),
    }


def create(*, preflight_package_id: str, content_text: str) -> dict[str, Any]:
    text = _content(content_text)
    first = _current_bindings(str(preflight_package_id))
    content_sha = hashlib.sha256(text.encode()).hexdigest()
    stable = (
        "\0".join(
            str(first[key])
            for key in (
                "tenant_id",
                "user_id",
                "application_id",
                "preflight_package_digest",
                "job_snapshot_sha256",
                "candidate_profile_digest",
                "resume_artifact_sha256",
            )
        )
        + f"\0{content_sha}\0{ARTIFACT_VERSION}"
    )
    cover_letter_id = "cover-letter-" + hashlib.sha256(stable.encode()).hexdigest()[:32]
    artifact_reference = f"hunter-cover-letter://{cover_letter_id}/pdf"

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        prior = connection.execute(
            "SELECT * FROM cover_letter_artifacts_v1 "
            "WHERE cover_letter_id=? AND tenant_id=? AND user_id=?",
            (cover_letter_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if prior is not None:
            prior_bytes = bytes(prior["content"])
            checks = (
                str(prior["application_id"]) == str(first["application_id"]),
                str(prior["preflight_package_id"])
                == str(first["preflight_package_id"]),
                str(prior["preflight_package_digest"])
                == str(first["preflight_package_digest"]),
                int(prior["job_id"]) == int(first["job_id"]),
                str(prior["job_snapshot_sha256"]) == str(first["job_snapshot_sha256"]),
                str(prior["candidate_profile_digest"])
                == str(first["candidate_profile_digest"]),
                str(prior["resume_version_id"]) == str(first["resume_version_id"]),
                str(prior["resume_artifact_sha256"])
                == str(first["resume_artifact_sha256"]),
                str(prior["content_sha256"]) == content_sha,
                str(prior["content_text"]) == text,
                str(prior["artifact_reference"]) == artifact_reference,
                _sha(prior_bytes) == str(prior["artifact_sha256"]),
            )
            if not all(checks):
                raise RuntimeError(
                    "Immutable cover-letter artifact no longer matches its logical authority binding."
                )
            return _payload(prior)
    finally:
        connection.close()

    data, pages = _render_pdf(text, company=first["company"], title=first["title"])
    second = _current_bindings(str(preflight_package_id))
    binding_keys = (
        "tenant_id",
        "user_id",
        "application_id",
        "preflight_package_id",
        "preflight_package_digest",
        "job_id",
        "job_snapshot_sha256",
        "candidate_profile_digest",
        "resume_version_id",
        "resume_artifact_sha256",
    )
    if any(first[key] != second[key] for key in binding_keys):
        raise RuntimeError("Cover letter authority bindings changed during rendering.")

    artifact_sha = _sha(data)
    filename = _safe_filename(second["company"], second["title"])
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        prior = connection.execute(
            "SELECT * FROM cover_letter_artifacts_v1 "
            "WHERE cover_letter_id=? AND tenant_id=? AND user_id=?",
            (cover_letter_id, owner.tenant_id, owner.user_id),
        ).fetchone()
        if prior is not None:
            prior_bytes = bytes(prior["content"])
            if (
                str(prior["content_sha256"]) != content_sha
                or str(prior["content_text"]) != text
                or _sha(prior_bytes) != str(prior["artifact_sha256"])
            ):
                raise RuntimeError(
                    "Concurrent immutable cover-letter artifact conflicts with logical identity."
                )
            connection.commit()
            return _payload(prior)

        connection.execute(
            "INSERT INTO cover_letter_artifacts_v1("
            "cover_letter_id,tenant_id,user_id,application_id,preflight_package_id,"
            "preflight_package_digest,job_id,job_snapshot_sha256,candidate_profile_digest,"
            "resume_version_id,resume_artifact_sha256,content_sha256,artifact_sha256,filename,"
            "mime_type,byte_count,page_count,artifact_reference,content_text,content"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                cover_letter_id,
                owner.tenant_id,
                owner.user_id,
                second["application_id"],
                second["preflight_package_id"],
                second["preflight_package_digest"],
                second["job_id"],
                second["job_snapshot_sha256"],
                second["candidate_profile_digest"],
                second["resume_version_id"],
                second["resume_artifact_sha256"],
                content_sha,
                artifact_sha,
                filename,
                "application/pdf",
                len(data),
                pages,
                artifact_reference,
                text,
                data,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM cover_letter_artifacts_v1 WHERE cover_letter_id=?",
            (cover_letter_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("Cover-letter artifact disappeared after persistence.")
        return _payload(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_artifact(cover_letter_id: str) -> dict[str, Any]:
    c = get_connection()
    try:
        ensure_schema(c)
        owner = current_owner(c)
        row = c.execute(
            "SELECT * FROM cover_letter_artifacts_v1 WHERE cover_letter_id=? AND tenant_id=? AND user_id=?",
            (str(cover_letter_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                "Cover-letter artifact is not owned by the current tenant user."
            )
        return _payload(row)
    finally:
        c.close()


def artifact_bytes(cover_letter_id: str) -> bytes:
    c = get_connection()
    try:
        ensure_schema(c)
        owner = current_owner(c)
        row = c.execute(
            "SELECT artifact_sha256,content FROM cover_letter_artifacts_v1 WHERE cover_letter_id=? AND tenant_id=? AND user_id=?",
            (str(cover_letter_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError(
                "Cover-letter artifact is not owned by the current tenant user."
            )
        data = bytes(row["content"])
        if _sha(data) != str(row["artifact_sha256"]):
            raise RuntimeError("Stored cover-letter artifact digest is invalid.")
        return data
    finally:
        c.close()


def freshness(cover_letter_id: str) -> dict[str, Any]:
    a = get_artifact(str(cover_letter_id))
    reasons = []
    try:
        cur = _current_bindings(str(a["preflight_package_id"]))
        checks = {
            "application_id": a["application_id"],
            "preflight_package_digest": a["preflight_package_digest"],
            "job_id": a["job_id"],
            "job_snapshot_sha256": a["job_snapshot_sha256"],
            "candidate_profile_digest": a["candidate_profile_digest"],
            "resume_version_id": a["resume_version_id"],
            "resume_artifact_sha256": a["resume_artifact_sha256"],
        }
        for k, v in checks.items():
            if cur[k] != v:
                reasons.append(f"{k}_changed")
    except (LookupError, PermissionError, RuntimeError, ValueError) as e:
        reasons.append(f"revalidation_failed:{type(e).__name__}")
    return {
        "cover_letter_id": str(cover_letter_id),
        "fresh": not reasons,
        "reasons": reasons,
        "artifact_sha256": a["artifact_sha256"],
        "submission_authority": False,
    }
