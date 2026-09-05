"""Strengthened Phase 4 Native Resume Engine.

V4 preserves the proven V2 writer, ATS, truth-audit, renderer, and preparation-only
boundaries while binding every newly generated resume to the exact confirmed
Candidate Truth Profile state from Sections 1-3.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from app import native_resume_service as v1
from app import native_resume_service_v2 as v2
from app import phase45_truth_binding as truth_binding

SCHEMA_VERSION = "native-resume-studio-service-v4-truth-bound"
_MAX_PROFILE_EVIDENCE = 120
_MAX_PROFILE_EVIDENCE_TEXT = 1200


def ensure_schema(connection=None) -> None:
    truth_binding.ensure_schema(connection)


def _profile_evidence_item(fact: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, str]:
    value = fact.get("value")
    if isinstance(value, list):
        text = " | ".join(str(item).strip() for item in value if str(item).strip())
    else:
        text = " ".join(str(value or "").split())
    fact_id = str(fact["fact_id"])
    return {
        "evidence_id": f"truth:{fact_id}",
        "kind": "candidate_truth_profile_fact",
        "label": str(fact["key"]),
        "text": text[:_MAX_PROFILE_EVIDENCE_TEXT],
        "source_reference": (
            f"candidate://truth-profile/{snapshot['source_extraction_id']}"
            f"/r{snapshot['profile_revision']}/{fact_id}"
        ),
    }


def _truth_bound_evidence_bundle(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Extend the V1 evidence bundle with safe resolved Candidate Truth facts.

    Protected facts and application-default facts are excluded. The resulting
    digest is recomputed so the persisted resume evidence digest covers both the
    immutable source and the exact resolved profile facts available to the writer.
    """
    bundle = copy.deepcopy(v1.build_evidence_bundle())
    context = truth_binding.safe_resume_profile_context(snapshot)
    seen = {str(item["evidence_id"]) for item in bundle["items"]}
    for fact in context["facts"][:_MAX_PROFILE_EVIDENCE]:
        item = _profile_evidence_item(fact, snapshot)
        if not item["text"] or item["evidence_id"] in seen:
            continue
        bundle["items"].append(item)
        seen.add(item["evidence_id"])
    canonical = json.dumps(bundle["items"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    bundle["evidence_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    bundle["candidate_truth_binding"] = truth_binding.public_binding_state(snapshot)
    return bundle


def _profile_prompt_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    context = truth_binding.safe_resume_profile_context(snapshot)
    # Prompt facts reference the same evidence IDs accepted by the truth audit.
    return {
        "source_extraction_id": context["source_extraction_id"],
        "profile_revision": context["profile_revision"],
        "profile_digest": context["profile_digest"],
        "facts": [
            {
                **fact,
                "resume_evidence_id": f"truth:{fact['fact_id']}",
            }
            for fact in context["facts"]
        ],
    }


def get_version(version_id: str) -> dict[str, Any]:
    record = v1.get_version(version_id)
    binding = truth_binding.resume_truth_binding(version_id)
    record["candidate_truth_binding"] = {
        key: binding[key]
        for key in (
            "source_extraction_id",
            "profile_revision",
            "profile_digest",
            "source_profile_sha256",
            "source_resume_sha256",
        )
        if binding and key in binding
    }
    record["candidate_truth_bound"] = bool(binding)
    return record


def list_versions(*, job_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    records = v1.list_versions(job_id=job_id, limit=limit)
    for record in records:
        binding = truth_binding.resume_truth_binding(str(record["version_id"]))
        record["candidate_truth_bound"] = bool(binding)
        if binding:
            record["profile_revision"] = int(binding["profile_revision"])
            record["profile_digest"] = str(binding["profile_digest"])
            record["source_extraction_id"] = str(binding["source_extraction_id"])
    return records


def generate_resume(
    *,
    job_id: int,
    instruction: str = "",
    rewrite_mode: str = "medium",
    parent_version_id: str | None = None,
    locked_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Generate an immutable resume atomically bound to current Candidate Truth."""
    snapshot = truth_binding.current_candidate_profile_snapshot()
    mode = str(rewrite_mode or "medium").strip().casefold()
    policy = v2.rewrite_policy(mode)
    context = v1.job_context(int(job_id))
    bundle = _truth_bound_evidence_bundle(snapshot)
    instruction_text = str(instruction or "").strip()
    if len(instruction_text) > v1._MAX_INSTRUCTION_CHARS:
        raise ValueError(f"Revision instruction must be at most {v1._MAX_INSTRUCTION_CHARS:,} characters.")
    locks = sorted({str(value).strip().casefold() for value in (locked_sections or []) if str(value).strip()})
    if any(value not in v1._LOCKABLE for value in locks):
        raise ValueError("Unsupported locked resume section.")

    parent_document: v1.ResumeDocument | None = None
    if parent_version_id:
        parent = get_version(parent_version_id)
        if int(parent["job_id"]) != int(job_id):
            raise ValueError("A revision must remain attached to the same job.")
        truth_binding.assert_parent_truth_current(parent_version_id, snapshot)
        parent_document = v1.ResumeDocument.model_validate(parent["document"])

    config = v2._resolve_writer_config()
    prompt_payload: dict[str, Any] = {
        "task": "revise_resume" if parent_document else "generate_resume",
        "rewrite_mode": mode,
        "rewrite_policy": policy,
        "job": context,
        "candidate_truth_profile": _profile_prompt_context(snapshot),
        "evidence_bundle": {
            "source_id": bundle["source_id"],
            "source_label": bundle["source_label"],
            "evidence_digest": bundle["evidence_digest"],
            "items": bundle["items"],
        },
        "instruction": instruction_text or (
            "Tailor this resume to the selected job description within the selected rewrite-strength policy. "
            "Use only evidence IDs supplied in this payload."
        ),
        "locked_sections": locks,
        "current_resume": parent_document.model_dump() if parent_document else None,
    }

    candidate_payload, response_id, model = v2._call_openai_v2(
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
        candidate_payload, repair_response_id, model = v2._call_openai_v2(
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

    diagnostics.update(
        {
            "schema_version": "native-resume-diagnostics-v4-truth-bound",
            "rewrite_mode": mode,
            "writer_model": model,
            "writer_reasoning_effort": config["reasoning_effort"],
            "writer_api_calls": calls_used,
            "writer_api_call_limit": int(config["max_calls_per_generation"]),
            "writer_key_source": config["key_source"],
            "candidate_truth_binding": truth_binding.public_binding_state(snapshot),
        }
    )

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
        truth_binding.save_resume_truth_binding(connection, version_id=version_id, snapshot=snapshot)
        connection.commit()
        return get_version(version_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


# Re-export stable V2/V1 controls and artifact helpers.
MODEL_OPTIONS = v2.MODEL_OPTIONS
REWRITE_PRESETS = v2.REWRITE_PRESETS
REWRITE_MODES = v2.REWRITE_MODES
REASONING_OPTIONS = v2.REASONING_OPTIONS
active_source = v2.active_source
build_evidence_bundle = v2.build_evidence_bundle
delete_personal_api_key = v2.delete_personal_api_key
extract_uploaded_source = v2.extract_uploaded_source
job_context = v2.job_context
native_resume_authority_enabled = v2.native_resume_authority_enabled
resume_job_options = v2.resume_job_options
safe_filename = v2.safe_filename
save_confirmed_source = v2.save_confirmed_source
save_personal_api_key = v2.save_personal_api_key
save_writer_settings = v2.save_writer_settings
version_diff = v2.version_diff
version_docx = v2.version_docx
version_html = v2.version_html
version_pdf = v2.version_pdf
writer_status = v2.writer_status
