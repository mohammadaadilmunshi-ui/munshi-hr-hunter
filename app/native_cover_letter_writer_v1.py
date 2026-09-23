"""Truth-bound Native Cover Letter Writer V1.

The writer is preparation-only.

It:
- binds generation to a fresh READY_TO_APPLY preflight package;
- binds to current Candidate Truth, job snapshot, Resume V5 and JD plan;
- permits only supplied Candidate evidence IDs;
- permits only supported JD requirement IDs;
- rejects do-not-claim requirements;
- rejects unsupported quantified candidate claims;
- rechecks Candidate Truth, job and JD-plan freshness after the model call;
- delegates immutable one-page PDF authority to cover_letter_artifact_v1;
- persists a compact audit record, not secret credentials;
- grants no final-review or submission authority.

It never browses an ATS, sends email, approves review, or submits an application.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app import cover_letter_artifact_v1
from app import jd_resume_plan_v1
from app import native_resume_service as resume_v1
from app import native_resume_service_v2 as resume_v2
from app import native_resume_service_v4 as resume_v4
from app import phase45_truth_binding as truth_binding
from app import application_preflight_package_v1 as preflight_v1
from app.database import get_connection
from app.phase67_common import safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import current_owner
from app.tenant_foundation import ensure_schema as ensure_tenant_schema


WRITER_VERSION = "munshi-native-cover-letter-writer-v1"
SUBMISSION_AUTHORITY = False

_SUPPORTED_MATCH = frozenset({"DIRECT", "STRONG_TRANSFERABLE", "PARTIAL"})
_MAX_PARAGRAPHS = 4
_MAX_PARAGRAPH_CHARS = 1300
_MAX_CONTENT_CHARS = 4300
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\$?\d[\d,]*(?:\.\d+)?%?\+?")
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS native_cover_letter_generations_v1(
        generation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        application_id TEXT NOT NULL,
        preflight_package_id TEXT NOT NULL,
        preflight_package_digest TEXT NOT NULL CHECK(length(preflight_package_digest)=64),
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        candidate_profile_digest TEXT NOT NULL CHECK(length(candidate_profile_digest)=64),
        resume_version_id TEXT NOT NULL,
        jd_plan_id TEXT NOT NULL,
        jd_plan_digest TEXT NOT NULL CHECK(length(jd_plan_digest)=64),
        evidence_digest TEXT NOT NULL CHECK(length(evidence_digest)=64),
        writer_version TEXT NOT NULL,
        model_name TEXT NOT NULL,
        model_response_id TEXT,
        model_calls INTEGER NOT NULL CHECK(model_calls>=1),
        content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
        cover_letter_id TEXT NOT NULL,
        artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256)=64),
        audit_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(
            tenant_id,user_id,preflight_package_id,jd_plan_digest,
            evidence_digest,writer_version
        ),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_native_cover_letter_generation_owner_app
       ON native_cover_letter_generations_v1(
           tenant_id,user_id,application_id,created_at DESC
       );""",
)


class CoverLetterParagraph(BaseModel):
    text: str = Field(min_length=30, max_length=_MAX_PARAGRAPH_CHARS)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    requirement_ids: list[str] = Field(min_length=1, max_length=8)


class CoverLetterDraft(BaseModel):
    paragraphs: list[CoverLetterParagraph] = Field(
        min_length=2,
        max_length=_MAX_PARAGRAPHS,
    )


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        cover_letter_artifact_v1.ensure_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip()).casefold()


def _number_tokens(value: str) -> set[str]:
    return {
        token.replace(",", "").casefold()
        for token in _NUMBER_RE.findall(str(value or ""))
    }


def _current_context(preflight_package_id: str) -> dict[str, Any]:
    package_id = str(preflight_package_id or "").strip()
    if not package_id:
        raise ValueError("Cover-letter preflight package id is required.")

    package = preflight_v1.get_preflight_package(package_id)
    freshness = preflight_v1.preflight_package_freshness(package_id)
    if freshness.get("fresh") is not True:
        raise RuntimeError("Native cover-letter preflight package is stale.")
    if str(package.get("status") or "") != "READY_TO_APPLY":
        raise ValueError(
            "Native cover-letter generation requires a READY_TO_APPLY preflight package."
        )

    snapshot = truth_binding.current_candidate_profile_snapshot()
    candidate_binding = truth_binding.public_binding_state(snapshot)

    package_snapshot = dict(package.get("snapshot") or {})
    application_truth = dict(package_snapshot.get("application_truth") or {})
    package_candidate = dict(
        application_truth.get("candidate_profile_binding") or {}
    )

    if str(package_candidate.get("profile_digest") or "") != str(
        candidate_binding.get("profile_digest") or ""
    ):
        raise RuntimeError(
            "Candidate Truth changed after the preflight package was prepared."
        )

    job_id = int(package["job_id"])
    owned_job = safe_owned_job_snapshot(job_id)
    job_digest = str(owned_job["job_snapshot_sha256"])
    if job_digest != str(package["job_snapshot_sha256"]):
        raise RuntimeError(
            "The job changed after the preflight package was prepared."
        )

    jd_plan = jd_resume_plan_v1.plan_for_job(job_id, persist=True)
    if (
        str(jd_plan.get("job_snapshot_sha256") or "") != job_digest
        or str(
            dict(jd_plan.get("candidate_truth_binding") or {}).get(
                "profile_digest"
            )
            or ""
        )
        != str(candidate_binding.get("profile_digest") or "")
    ):
        raise RuntimeError(
            "JD plan is not bound to the same Candidate Truth and job state."
        )

    bundle = resume_v4._truth_bound_evidence_bundle(snapshot)
    evidence_items = [
        dict(item)
        for item in (bundle.get("items") or [])
        if isinstance(item, Mapping)
    ]
    evidence_map = {
        str(item.get("evidence_id") or ""): item
        for item in evidence_items
        if str(item.get("evidence_id") or "").strip()
    }
    if not evidence_map:
        raise ValueError("Native cover-letter Candidate evidence is empty.")

    requirement_refs = [
        dict(item)
        for item in (jd_plan.get("requirement_refs") or [])
        if isinstance(item, Mapping)
    ]
    requirement_map = {
        str(item.get("requirement_id") or ""): item
        for item in requirement_refs
        if str(item.get("requirement_id") or "").strip()
    }
    supported_requirement_ids = {
        requirement_id
        for requirement_id, item in requirement_map.items()
        if str(item.get("match_status") or "") in _SUPPORTED_MATCH
    }
    forbidden_requirement_ids = {
        str(value)
        for value in (jd_plan.get("do_not_claim_requirement_ids") or [])
    }

    if not supported_requirement_ids:
        raise ValueError(
            "The selected job has no evidence-supported requirement available "
            "for a truthful cover letter."
        )

    return {
        "package": package,
        "job": dict(owned_job["job"]),
        "job_id": job_id,
        "job_snapshot_sha256": job_digest,
        "candidate_snapshot": snapshot,
        "candidate_binding": candidate_binding,
        "resume_version_id": str(package["resume_version_id"]),
        "jd_plan": jd_plan,
        "evidence_bundle": bundle,
        "evidence_map": evidence_map,
        "requirement_map": requirement_map,
        "supported_requirement_ids": supported_requirement_ids,
        "forbidden_requirement_ids": forbidden_requirement_ids,
    }


def _assert_context_still_current(context: Mapping[str, Any]) -> None:
    snapshot = context["candidate_snapshot"]
    current_snapshot = truth_binding.current_candidate_profile_snapshot()
    if not truth_binding.binding_matches_snapshot(
        truth_binding.public_binding_state(snapshot),
        current_snapshot,
    ):
        raise RuntimeError(
            "Candidate Truth changed during cover-letter generation."
        )

    current_job = safe_owned_job_snapshot(int(context["job_id"]))
    if str(current_job["job_snapshot_sha256"]) != str(
        context["job_snapshot_sha256"]
    ):
        raise RuntimeError("Job changed during cover-letter generation.")

    plan_state = jd_resume_plan_v1.plan_freshness(
        str(context["jd_plan"]["plan_id"])
    )
    if plan_state.get("fresh") is not True:
        raise RuntimeError(
            "JD plan became stale during cover-letter generation."
        )


def _writer_payload(context: Mapping[str, Any], *, repair: str | None = None) -> dict[str, Any]:
    plan = context["jd_plan"]
    supported = context["supported_requirement_ids"]
    requirement_refs = [
        {
            "requirement_id": ref["requirement_id"],
            "priority": ref.get("priority"),
            "type": ref.get("type"),
            "exact_text": ref.get("exact_text"),
            "match_status": ref.get("match_status"),
            "evidence_ids": list(ref.get("evidence_ids") or []),
        }
        for ref in plan.get("requirement_refs") or []
        if str(ref.get("requirement_id") or "") in supported
    ]

    evidence_items = [
        {
            "evidence_id": item.get("evidence_id"),
            "kind": item.get("kind"),
            "label": item.get("label"),
            "text": item.get("text"),
        }
        for item in context["evidence_map"].values()
    ]

    payload = {
        "task": "generate_truth_bound_cover_letter",
        "job": context["job"],
        "job_snapshot_sha256": context["job_snapshot_sha256"],
        "candidate_truth_binding": context["candidate_binding"],
        "supported_requirements": requirement_refs,
        "do_not_claim_requirement_ids": sorted(
            context["forbidden_requirement_ids"]
        ),
        "candidate_evidence": evidence_items,
        "rules": [
            "Return 2 to 4 concise paragraphs only.",
            "Every paragraph must cite candidate evidence IDs supplied here.",
            "Every paragraph must cite supported requirement IDs supplied here.",
            "Never claim a do-not-claim requirement.",
            "Never invent candidate facts, metrics, tenure, credentials, tools, "
            "authorization, sponsorship, education, employers, titles, or achievements.",
            "Do not mention a number, percentage, dollar amount, date, duration, "
            "or quantity unless that exact token appears in the cited candidate evidence.",
            "Write naturally in first person, without AI/meta language.",
            "Do not add a salutation or signature; MUNSHI adds those deterministically.",
            "Keep the full letter concise enough for one US Letter page.",
        ],
        "repair_instruction": repair,
        "submission_authority": False,
    }
    return payload


def _system_prompt() -> str:
    return (
        "You write a concise professional cover letter using only supplied "
        "Candidate Truth evidence. Treat evidence IDs and supported JD "
        "requirements as the complete factual universe. Unsupported claims are "
        "forbidden. Return only the requested JSON schema."
    )


def _call_writer(
    *,
    prompt_payload: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    body = {
        "model": config["model"],
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": min(int(config["max_output_tokens"]), 5000),
        "input": [
            {
                "role": "system",
                "content": [
                    {"type": "input_text", "text": _system_prompt()}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            dict(prompt_payload),
                            ensure_ascii=False,
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "munshi_native_cover_letter_v1",
                # Match the already-proven Resume V2 structured-output transport.
                # Post-response Pydantic + truth guards remain authoritative.
                "strict": False,
                "schema": CoverLetterDraft.model_json_schema(),
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as error:
        raise RuntimeError(
            "OpenAI cover-letter generation failed with HTTP "
            f"{error.response.status_code}."
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise RuntimeError(
            "OpenAI cover-letter generation could not be completed."
        ) from error

    payload = resume_v1._json_from_model_text(
        resume_v1._response_text(data)
    )
    return (
        payload,
        str(data.get("id") or "")[:200],
        str(config["model"]),
    )


def _validate_draft(
    draft_payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> tuple[CoverLetterDraft, dict[str, Any]]:
    draft = CoverLetterDraft.model_validate(draft_payload)
    evidence_map = context["evidence_map"]
    requirement_map = context["requirement_map"]
    supported = context["supported_requirement_ids"]
    forbidden = context["forbidden_requirement_ids"]

    used_evidence: set[str] = set()
    used_requirements: set[str] = set()

    for paragraph in draft.paragraphs:
        evidence_ids = list(dict.fromkeys(paragraph.evidence_ids))
        requirement_ids = list(dict.fromkeys(paragraph.requirement_ids))

        unknown_evidence = sorted(
            evidence_id
            for evidence_id in evidence_ids
            if evidence_id not in evidence_map
        )
        if unknown_evidence:
            raise ValueError(
                "Cover letter cited unknown Candidate evidence: "
                + ", ".join(unknown_evidence)
            )

        unknown_requirements = sorted(
            requirement_id
            for requirement_id in requirement_ids
            if requirement_id not in requirement_map
        )
        if unknown_requirements:
            raise ValueError(
                "Cover letter cited unknown JD requirements: "
                + ", ".join(unknown_requirements)
            )

        disallowed_requirements = sorted(
            requirement_id
            for requirement_id in requirement_ids
            if requirement_id not in supported
            or requirement_id in forbidden
        )
        if disallowed_requirements:
            raise ValueError(
                "Cover letter attempted to claim unsupported JD requirements: "
                + ", ".join(disallowed_requirements)
            )

        support_text = " ".join(
            str(evidence_map[evidence_id].get("text") or "")
            for evidence_id in evidence_ids
        )
        support_numbers = _number_tokens(support_text)
        paragraph_numbers = _number_tokens(paragraph.text)
        unsupported_numbers = sorted(paragraph_numbers - support_numbers)
        if unsupported_numbers:
            raise ValueError(
                "Cover letter contains unsupported quantified candidate claims: "
                + ", ".join(unsupported_numbers)
            )

        normalized_paragraph = _normalized(paragraph.text)
        for requirement_id in forbidden:
            ref = requirement_map.get(requirement_id)
            exact = _normalized(
                ref.get("exact_text") if isinstance(ref, Mapping) else ""
            )
            if len(exact) >= 12 and exact in normalized_paragraph:
                raise ValueError(
                    "Cover letter contains a forbidden unsupported requirement "
                    f"claim: {requirement_id}"
                )

        used_evidence.update(evidence_ids)
        used_requirements.update(requirement_ids)

    content = _content_text(draft)
    if len(content) > _MAX_CONTENT_CHARS:
        raise ValueError(
            "Cover-letter draft exceeds the one-page content budget."
        )

    audit = {
        "writer_version": WRITER_VERSION,
        "evidence_ids": sorted(used_evidence),
        "requirement_ids": sorted(used_requirements),
        "do_not_claim_requirement_ids": sorted(forbidden),
        "unsupported_numeric_claims": [],
        "submission_authority": False,
    }
    return draft, audit


def _remove_forbidden_wording(
    draft_payload: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Remove only explicitly forbidden JD language; never add candidate facts.

    This is the bounded final repair after the model's single truth-repair
    attempt.  A paragraph that cannot retain both valid evidence and a supported
    requirement is removed.  The ordinary schema/truth validator then decides
    whether the reduced draft is still a usable cover letter.
    """
    draft = CoverLetterDraft.model_validate(draft_payload)
    forbidden = set(context["forbidden_requirement_ids"])
    supported = set(context["supported_requirement_ids"])
    requirement_map = context["requirement_map"]
    forbidden_text = tuple(
        _normalized((requirement_map.get(requirement_id) or {}).get("exact_text"))
        for requirement_id in forbidden
        if _normalized((requirement_map.get(requirement_id) or {}).get("exact_text"))
    )
    repaired: list[dict[str, Any]] = []
    for paragraph in draft.paragraphs:
        requirement_ids = [
            value
            for value in dict.fromkeys(paragraph.requirement_ids)
            if value in supported and value not in forbidden
        ]
        if not requirement_ids:
            continue
        sentences = _SENTENCE_BOUNDARY_RE.split(" ".join(paragraph.text.split()))
        kept = [
            sentence
            for sentence in sentences
            if not any(
                exact and exact in _normalized(sentence)
                for exact in forbidden_text
            )
        ]
        text = " ".join(value.strip() for value in kept if value.strip()).strip()
        if len(text) < 30:
            continue
        repaired.append(
            {
                "text": text,
                "evidence_ids": list(dict.fromkeys(paragraph.evidence_ids)),
                "requirement_ids": requirement_ids,
            }
        )
    return {"paragraphs": repaired}


def _content_text(draft: CoverLetterDraft) -> str:
    paragraphs = [
        " ".join(paragraph.text.split())
        for paragraph in draft.paragraphs
    ]
    return (
        "Dear Hiring Team,\n\n"
        + "\n\n".join(paragraphs)
        + "\n\nSincerely,"
    )


def _existing_generation(
    *,
    preflight_package_id: str,
    jd_plan_digest: str,
    evidence_digest: str,
) -> sqlite3.Row | None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        return connection.execute(
            """SELECT * FROM native_cover_letter_generations_v1
               WHERE tenant_id=? AND user_id=?
                 AND preflight_package_id=?
                 AND jd_plan_digest=?
                 AND evidence_digest=?
                 AND writer_version=?""",
            (
                owner.tenant_id,
                owner.user_id,
                preflight_package_id,
                jd_plan_digest,
                evidence_digest,
                WRITER_VERSION,
            ),
        ).fetchone()
    finally:
        connection.close()


def _generation_payload(row: sqlite3.Row) -> dict[str, Any]:
    artifact = cover_letter_artifact_v1.get_artifact(
        str(row["cover_letter_id"])
    )
    fresh = cover_letter_artifact_v1.freshness(
        str(row["cover_letter_id"])
    )
    if fresh.get("fresh") is not True:
        raise RuntimeError(
            "Existing Native cover-letter artifact became stale."
        )
    return {
        "generation_id": str(row["generation_id"]),
        "version": WRITER_VERSION,
        "application_id": str(row["application_id"]),
        "preflight_package_id": str(row["preflight_package_id"]),
        "job_id": int(row["job_id"]),
        "job_snapshot_sha256": str(row["job_snapshot_sha256"]),
        "candidate_profile_digest": str(row["candidate_profile_digest"]),
        "resume_version_id": str(row["resume_version_id"]),
        "jd_plan_id": str(row["jd_plan_id"]),
        "jd_plan_digest": str(row["jd_plan_digest"]),
        "evidence_digest": str(row["evidence_digest"]),
        "model_name": str(row["model_name"]),
        "model_response_id": str(row["model_response_id"] or ""),
        "model_calls": int(row["model_calls"]),
        "content_sha256": str(row["content_sha256"]),
        "cover_letter_id": str(row["cover_letter_id"]),
        "artifact_id": str(artifact["artifact_id"]),
        "artifact_reference": str(artifact["artifact_reference"]),
        "artifact_sha256": str(artifact["artifact_sha256"]),
        "filename": str(artifact["filename"]),
        "mime_type": str(artifact["mime_type"]),
        "page_count": int(artifact["page_count"]),
        "immutable": True,
        "submission_authority": False,
        "automatic_submission_executed": False,
    }


def generate(
    *,
    preflight_package_id: str,
) -> dict[str, Any]:
    context = _current_context(preflight_package_id)
    package = context["package"]
    plan = context["jd_plan"]
    bundle = context["evidence_bundle"]

    prior = _existing_generation(
        preflight_package_id=str(package["package_id"]),
        jd_plan_digest=str(plan["plan_digest"]),
        evidence_digest=str(bundle["evidence_digest"]),
    )
    if prior is not None:
        return _generation_payload(prior)

    config = resume_v2._resolve_writer_config()
    prompt = _writer_payload(context)
    candidate, response_id, model = _call_writer(
        prompt_payload=prompt,
        config=config,
    )
    model_calls = 1
    try:
        draft, audit = _validate_draft(candidate, context)
    except ValueError as validation_error:
        if model_calls >= int(config["max_calls_per_generation"]):
            raise
        repair_prompt = _writer_payload(
            context,
            repair=(
                "The previous draft failed the authoritative truth check. "
                "Remove or rewrite only the unsupported wording using supplied "
                "Candidate Truth evidence and supported requirements. If a claim "
                "cannot be supported, remove it. Add no facts. Validation detail: "
                + str(validation_error)[:500]
            ),
        )
        repair_prompt["previous_draft"] = candidate
        candidate, response_id, model = _call_writer(
            prompt_payload=repair_prompt,
            config=config,
        )
        model_calls += 1
        try:
            draft, audit = _validate_draft(candidate, context)
        except ValueError as final_validation_error:
            if not any(
                marker in str(final_validation_error).casefold()
                for marker in (
                    "forbidden unsupported requirement",
                    "unsupported jd requirements",
                )
            ):
                raise
            candidate = _remove_forbidden_wording(candidate, context)
            try:
                draft, audit = _validate_draft(candidate, context)
            except ValueError as deterministic_repair_error:
                raise final_validation_error from deterministic_repair_error
            audit["deterministic_truth_repair"] = (
                "unsupported_requirement_wording_removed"
            )
    content = _content_text(draft)

    _assert_context_still_current(context)

    try:
        artifact = cover_letter_artifact_v1.create(
            preflight_package_id=str(package["package_id"]),
            content_text=content,
        )
    except ValueError as error:
        if (
            "exactly one page" not in str(error).casefold()
            or model_calls >= int(config["max_calls_per_generation"])
        ):
            raise

        repair_prompt = _writer_payload(
            context,
            repair=(
                "The previous truthful draft exceeded one page. "
                "Shorten it materially while retaining the same factual claims "
                "and evidence/requirement citations. Add no facts."
            ),
        )
        repair_prompt["previous_draft"] = draft.model_dump()
        candidate, response_id, model = _call_writer(
            prompt_payload=repair_prompt,
            config=config,
        )
        model_calls += 1
        draft, audit = _validate_draft(candidate, context)
        content = _content_text(draft)
        _assert_context_still_current(context)
        artifact = cover_letter_artifact_v1.create(
            preflight_package_id=str(package["package_id"]),
            content_text=content,
        )

    if (
        int(artifact.get("page_count") or 0) != 1
        or artifact.get("submission_authority") is not False
        or str(artifact.get("job_snapshot_sha256") or "")
        != str(context["job_snapshot_sha256"])
        or str(artifact.get("candidate_profile_digest") or "")
        != str(context["candidate_binding"]["profile_digest"])
        or str(artifact.get("resume_version_id") or "")
        != str(context["resume_version_id"])
    ):
        raise RuntimeError(
            "Native cover-letter artifact authority binding is invalid."
        )

    content_sha = _sha_text(content)
    stable = {
        "writer_version": WRITER_VERSION,
        "preflight_package_id": package["package_id"],
        "preflight_package_digest": package["package_digest"],
        "job_snapshot_sha256": context["job_snapshot_sha256"],
        "candidate_profile_digest": context["candidate_binding"]["profile_digest"],
        "resume_version_id": context["resume_version_id"],
        "jd_plan_digest": plan["plan_digest"],
        "evidence_digest": bundle["evidence_digest"],
        "content_sha256": content_sha,
        "cover_letter_id": artifact["cover_letter_id"],
    }
    generation_id = (
        "native-cover-letter-"
        + sha256_json(stable)[:32]
    )
    audit_payload = {
        **audit,
        "model_name": model,
        "model_response_id": response_id,
        "model_calls": model_calls,
        "writer_key_source": config["key_source"],
        "content_chars": len(content),
        "page_count": 1,
    }

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if connection.in_transaction:
            connection.commit()
        connection.execute("BEGIN IMMEDIATE")

        existing = connection.execute(
            """SELECT * FROM native_cover_letter_generations_v1
               WHERE tenant_id=? AND user_id=?
                 AND preflight_package_id=?
                 AND jd_plan_digest=?
                 AND evidence_digest=?
                 AND writer_version=?""",
            (
                owner.tenant_id,
                owner.user_id,
                package["package_id"],
                plan["plan_digest"],
                bundle["evidence_digest"],
                WRITER_VERSION,
            ),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["cover_letter_id"])
                != str(artifact["cover_letter_id"])
                or str(existing["content_sha256"]) != content_sha
            ):
                raise RuntimeError(
                    "Concurrent Native cover-letter generation conflicts "
                    "with the existing immutable generation."
                )
            connection.commit()
            return _generation_payload(existing)

        connection.execute(
            """INSERT INTO native_cover_letter_generations_v1(
                generation_id,tenant_id,user_id,application_id,
                preflight_package_id,preflight_package_digest,
                job_id,job_snapshot_sha256,candidate_profile_digest,
                resume_version_id,jd_plan_id,jd_plan_digest,evidence_digest,
                writer_version,model_name,model_response_id,model_calls,
                content_sha256,cover_letter_id,artifact_sha256,audit_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                generation_id,
                owner.tenant_id,
                owner.user_id,
                package["application_id"],
                package["package_id"],
                package["package_digest"],
                int(context["job_id"]),
                context["job_snapshot_sha256"],
                context["candidate_binding"]["profile_digest"],
                context["resume_version_id"],
                plan["plan_id"],
                plan["plan_digest"],
                bundle["evidence_digest"],
                WRITER_VERSION,
                model,
                response_id or None,
                model_calls,
                content_sha,
                artifact["cover_letter_id"],
                artifact["artifact_sha256"],
                _canonical(audit_payload),
            ),
        )
        connection.commit()

        row = connection.execute(
            """SELECT * FROM native_cover_letter_generations_v1
               WHERE generation_id=?""",
            (generation_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError(
                "Native cover-letter generation disappeared after persistence."
            )
        return _generation_payload(row)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
