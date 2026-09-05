"""Personal OpenAI writer controls for MUNSHI Native Resume Studio V2.

This module layers candidate-scoped writer settings and rewrite-strength presets
on top of the proven V1 native resume service. It intentionally preserves the
V1 evidence, truth, one-page, immutable-version, and preparation-only authority
contracts.

OpenAI API keys are never written to Git, plaintext SQLite fields, diagnostics,
or Streamlit state. When a candidate saves a personal key it is encrypted by
``app.secure_vault`` using the server-side ``MUNSHI_VAULT_KEY``. A server-level
``OPENAI_API_KEY`` remains a fallback when no personal key is configured.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

import httpx

from app import native_resume_service as v1
from app.secure_vault import (
    VaultError,
    delete_secret,
    ensure_schema as ensure_vault_schema,
    read_secret,
    store_secret,
    vault_available,
)

SCHEMA_VERSION = "native-resume-studio-service-v2"
PERSONAL_KEY_TYPE = "openai_resume_api_key"
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_MAX_OUTPUT_TOKENS = 6000
DEFAULT_MAX_CALLS = 2
MODEL_OPTIONS = (
    "gpt-5.6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-sol",
)
REASONING_OPTIONS = ("low", "medium", "high")
REWRITE_MODES = ("slight", "medium", "aggressive")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")

REWRITE_PRESETS: dict[str, str] = {
    "slight": (
        "Use a light-touch rewrite. Preserve the candidate's existing structure, ordering, and most wording. "
        "Make only targeted JD-specific improvements to the summary, supported skills ordering, and directly relevant bullet phrasing. "
        "Avoid dropping substantial supported experience unless the one-page budget requires it."
    ),
    "medium": (
        "Use a balanced rewrite. Substantially improve the summary and bullet phrasing, reorder supported skills and evidence for the target JD, "
        "and condense lower-relevance content while preserving the candidate's truthful career narrative and recognizable resume structure."
    ),
    "aggressive": (
        "Use an aggressive but strictly truthful rewrite. Rebuild emphasis around the target JD, freely reorder, condense, or omit lower-relevance supported content, "
        "and rewrite the summary and bullets for the strongest evidence-backed alignment. Aggressive never permits invention, exaggeration, title inflation, "
        "new metrics, unsupported tools, unsupported skills, or altered dates."
    ),
}

WRITER_SCHEMA = """
CREATE TABLE IF NOT EXISTS native_resume_writer_settings(
    tenant_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT 'gpt-5.6-terra',
    reasoning_effort TEXT NOT NULL DEFAULT 'medium'
        CHECK(reasoning_effort IN ('low','medium','high')),
    max_output_tokens INTEGER NOT NULL DEFAULT 6000
        CHECK(max_output_tokens BETWEEN 2000 AND 12000),
    max_calls_per_generation INTEGER NOT NULL DEFAULT 2
        CHECK(max_calls_per_generation IN (1,2)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(tenant_id,user_id),
    FOREIGN KEY(tenant_id,user_id)
        REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
);
"""


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns_connection = connection is None
    connection = connection or v1.get_connection()
    try:
        v1.ensure_schema(connection)
        ensure_vault_schema(connection)
        connection.execute(WRITER_SCHEMA)
        if owns_connection:
            connection.commit()
    finally:
        if owns_connection:
            connection.close()


def _owner_vault_label(connection: sqlite3.Connection) -> str:
    owner = v1.current_owner(connection)
    return f"{owner.tenant_id}:{owner.user_id}:resume-studio"


def _saved_key_exists(connection: sqlite3.Connection, account_label: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM credential_secret WHERE credential_type=? AND account_label=? LIMIT 1",
        (PERSONAL_KEY_TYPE, account_label),
    ).fetchone()
    return row is not None


def _settings_row(connection: sqlite3.Connection) -> dict[str, Any]:
    owner = v1.current_owner(connection)
    row = connection.execute(
        """SELECT model_name,reasoning_effort,max_output_tokens,max_calls_per_generation,updated_at
           FROM native_resume_writer_settings WHERE tenant_id=? AND user_id=?""",
        (owner.tenant_id, owner.user_id),
    ).fetchone()
    if row:
        return dict(row)
    env_model = str(os.getenv(v1.MODEL_ENV) or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return {
        "model_name": env_model,
        "reasoning_effort": DEFAULT_REASONING_EFFORT,
        "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
        "max_calls_per_generation": DEFAULT_MAX_CALLS,
        "updated_at": None,
    }


def writer_status() -> dict[str, Any]:
    """Return non-secret candidate writer configuration state."""
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        label = _owner_vault_label(connection)
        personal_record = _saved_key_exists(connection, label)
        secure_storage = vault_available()
        env_configured = bool(str(os.getenv(v1.API_KEY_ENV) or "").strip())
        settings = _settings_row(connection)
        personal_usable = personal_record and secure_storage
        if personal_usable:
            key_source = "personal_encrypted"
        elif env_configured:
            key_source = "server_environment"
        elif personal_record:
            key_source = "personal_key_locked"
        else:
            key_source = "none"
        return {
            "configured": bool(personal_usable or env_configured),
            "key_source": key_source,
            "personal_key_saved": personal_record,
            "secure_storage_available": secure_storage,
            "model": settings["model_name"],
            "reasoning_effort": settings["reasoning_effort"],
            "max_output_tokens": int(settings["max_output_tokens"]),
            "max_calls_per_generation": int(settings["max_calls_per_generation"]),
            "provider": "OpenAI Responses API",
            "native_authority": False,
        }
    finally:
        connection.close()


def save_personal_api_key(secret: str) -> None:
    """Encrypt and store a candidate-scoped OpenAI key without returning it."""
    value = str(secret or "").strip()
    if not value or len(value) < 16 or len(value) > 4096:
        raise ValueError("Enter a valid non-empty OpenAI API key.")
    if not vault_available():
        raise VaultError("Encrypted credential storage is not configured for this runtime.")
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        label = _owner_vault_label(connection)
    finally:
        connection.close()
    store_secret(PERSONAL_KEY_TYPE, value, account_label=label)


def delete_personal_api_key() -> bool:
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        label = _owner_vault_label(connection)
    finally:
        connection.close()
    return delete_secret(PERSONAL_KEY_TYPE, account_label=label)


def save_writer_settings(
    *,
    model_name: str,
    reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    max_calls_per_generation: int = DEFAULT_MAX_CALLS,
) -> dict[str, Any]:
    model = str(model_name or "").strip()
    effort = str(reasoning_effort or "").strip().casefold()
    tokens = int(max_output_tokens)
    calls = int(max_calls_per_generation)
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("Model ID contains unsupported characters or is too long.")
    if effort not in REASONING_OPTIONS:
        raise ValueError("Reasoning effort must be low, medium, or high.")
    if not 2000 <= tokens <= 12000:
        raise ValueError("Max output tokens must be between 2,000 and 12,000.")
    if calls not in {1, 2}:
        raise ValueError("Max GPT calls per resume must be 1 or 2.")
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v1.current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT INTO native_resume_writer_settings(
                   tenant_id,user_id,model_name,reasoning_effort,max_output_tokens,max_calls_per_generation
               ) VALUES (?,?,?,?,?,?)
               ON CONFLICT(tenant_id,user_id) DO UPDATE SET
                   model_name=excluded.model_name,
                   reasoning_effort=excluded.reasoning_effort,
                   max_output_tokens=excluded.max_output_tokens,
                   max_calls_per_generation=excluded.max_calls_per_generation,
                   updated_at=CURRENT_TIMESTAMP""",
            (owner.tenant_id, owner.user_id, model, effort, tokens, calls),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return writer_status()


def _resolve_writer_config() -> dict[str, Any]:
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        settings = _settings_row(connection)
        label = _owner_vault_label(connection)
        personal_record = _saved_key_exists(connection, label)
    finally:
        connection.close()

    key = ""
    source = "none"
    if personal_record and vault_available():
        try:
            key = str(read_secret(PERSONAL_KEY_TYPE, account_label=label) or "").strip()
        except VaultError:
            raise
        if key:
            source = "personal_encrypted"
    if not key:
        key = str(os.getenv(v1.API_KEY_ENV) or "").strip()
        if key:
            source = "server_environment"
    if not key:
        raise RuntimeError("OpenAI is not configured for Resume Studio. Add a personal API key or configure the server credential.")

    return {
        "api_key": key,
        "key_source": source,
        "model": settings["model_name"],
        "reasoning_effort": settings["reasoning_effort"],
        "max_output_tokens": int(settings["max_output_tokens"]),
        "max_calls_per_generation": int(settings["max_calls_per_generation"]),
    }


def rewrite_policy(mode: str) -> str:
    value = str(mode or "medium").strip().casefold()
    if value not in REWRITE_MODES:
        raise ValueError("Rewrite strength must be Slight, Medium, or Aggressive.")
    return REWRITE_PRESETS[value]


def _system_prompt(mode: str) -> str:
    return v1._system_prompt() + "\nRewrite-strength policy for this run:\n" + rewrite_policy(mode) + "\n"


def _call_openai_v2(
    *,
    prompt_payload: dict[str, Any],
    config: dict[str, Any],
    rewrite_mode: str,
) -> tuple[dict[str, Any], str, str]:
    body = {
        "model": config["model"],
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": int(config["max_output_tokens"]),
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": _system_prompt(rewrite_mode)}]},
            {"role": "user", "content": [{"type": "input_text", "text": json.dumps(prompt_payload, ensure_ascii=False)}]},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "munshi_native_resume_v2",
                "strict": False,
                "schema": v1.ResumeDocument.model_json_schema(),
            }
        },
    }
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post("https://api.openai.com/v1/responses", headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(f"OpenAI resume generation failed with HTTP {error.response.status_code}.") from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError("OpenAI resume generation could not be completed.") from error
    return v1._json_from_model_text(v1._response_text(data)), str(data.get("id") or "")[:200], str(config["model"])


def generate_resume(
    *,
    job_id: int,
    instruction: str = "",
    rewrite_mode: str = "medium",
    parent_version_id: str | None = None,
    locked_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Generate a validated immutable resume version with an explicit rewrite strength."""
    mode = str(rewrite_mode or "medium").strip().casefold()
    policy = rewrite_policy(mode)
    context = v1.job_context(int(job_id))
    bundle = v1.build_evidence_bundle()
    instruction_text = str(instruction or "").strip()
    if len(instruction_text) > v1._MAX_INSTRUCTION_CHARS:
        raise ValueError(f"Revision instruction must be at most {v1._MAX_INSTRUCTION_CHARS:,} characters.")
    locks = sorted({str(value).strip().casefold() for value in (locked_sections or []) if str(value).strip()})
    if any(value not in v1._LOCKABLE for value in locks):
        raise ValueError("Unsupported locked resume section.")

    parent_document: v1.ResumeDocument | None = None
    if parent_version_id:
        parent = v1.get_version(parent_version_id)
        if int(parent["job_id"]) != int(job_id):
            raise ValueError("A revision must remain attached to the same job.")
        parent_document = v1.ResumeDocument.model_validate(parent["document"])

    config = _resolve_writer_config()
    prompt_payload: dict[str, Any] = {
        "task": "revise_resume" if parent_document else "generate_resume",
        "rewrite_mode": mode,
        "rewrite_policy": policy,
        "job": context,
        "evidence_bundle": {
            "source_id": bundle["source_id"],
            "source_label": bundle["source_label"],
            "evidence_digest": bundle["evidence_digest"],
            "items": bundle["items"],
        },
        "instruction": instruction_text or "Tailor this resume to the selected job description within the selected rewrite-strength policy.",
        "locked_sections": locks,
        "current_resume": parent_document.model_dump() if parent_document else None,
    }

    candidate_payload, response_id, model = _call_openai_v2(
        prompt_payload=prompt_payload,
        config=config,
        rewrite_mode=mode,
    )
    calls_used = 1
    proposed = v1._apply_locks(v1.ResumeDocument.model_validate(candidate_payload), parent_document, locks)
    diagnostics = v1.analyze_document(proposed, context, bundle)

    if diagnostics["content_budget_issues"]:
        if int(config["max_calls_per_generation"]) < 2:
            raise ValueError(
                "The generated resume needs one repair pass to meet the one-page content budget, but your GPT call limit is 1. "
                "Increase the per-resume call limit to 2 or try a more conservative rewrite."
            )
        repair_payload = dict(prompt_payload)
        repair_payload["task"] = "repair_resume_content_budget"
        repair_payload["current_resume"] = proposed.model_dump()
        repair_payload["instruction"] = (
            "Repair only these content-budget issues while preserving facts and evidence references. "
            "Do not add new facts: " + ", ".join(diagnostics["content_budget_issues"])
        )
        candidate_payload, repair_response_id, model = _call_openai_v2(
            prompt_payload=repair_payload,
            config=config,
            rewrite_mode=mode,
        )
        calls_used += 1
        proposed = v1._apply_locks(v1.ResumeDocument.model_validate(candidate_payload), parent_document, locks)
        diagnostics = v1.analyze_document(proposed, context, bundle)
        if diagnostics["content_budget_issues"]:
            raise ValueError("Generated resume still exceeds the ATS content budget after one repair pass.")
        response_id = repair_response_id or response_id

    diagnostics.update({
        "schema_version": "native-resume-diagnostics-v2",
        "rewrite_mode": mode,
        "writer_model": model,
        "writer_reasoning_effort": config["reasoning_effort"],
        "writer_api_calls": calls_used,
        "writer_api_call_limit": int(config["max_calls_per_generation"]),
        "writer_key_source": config["key_source"],
    })

    html = v1.render_ats_html(proposed)
    connection = v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v1.current_owner(connection)
        v1._commit_schema_before_write(connection)
        connection.execute("BEGIN IMMEDIATE")
        version_number = v1._next_version_number(connection, owner.tenant_id, owner.user_id, int(job_id))
        version_id = f"native-resume-{v1.uuid4()}"
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
                v1._sha256_text(html),
            ),
        )
        connection.commit()
        return v1._owner_version(connection, version_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# Re-export the proven V1 preparation/read/download helpers so the V2 page keeps
# the same evidence and immutable-artifact behavior without duplicating them.
active_source = v1.active_source
build_evidence_bundle = v1.build_evidence_bundle
extract_uploaded_source = v1.extract_uploaded_source
get_version = v1.get_version
job_context = v1.job_context
list_versions = v1.list_versions
native_resume_authority_enabled = v1.native_resume_authority_enabled
resume_job_options = v1.resume_job_options
safe_filename = v1.safe_filename
save_confirmed_source = v1.save_confirmed_source
version_diff = v1.version_diff
version_docx = v1.version_docx
version_html = v1.version_html
version_pdf = v1.version_pdf
