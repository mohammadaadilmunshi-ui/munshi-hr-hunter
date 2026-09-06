"""Stage B-aware Native Resume Engine V5.

V5 preserves V4 Candidate Truth/job binding, evidence validation, numeric-claim
guards, rewrite controls, content-budget repair and immutable resume versions.
It adds one exact Stage B Resume Tailoring Plan to the writer input and atomically
persists the resume's Stage B plan binding plus internal JD requirement ↔ claim
trace in the same transaction as the resume/truth/job bindings.

V5 remains preparation-only. It has no browser, ATS, Gmail, n8n, outreach, or
application-submission authority.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from app import jd_claim_trace_v1 as claim_trace
from app import jd_resume_plan_v1 as planner
from app import native_resume_service_v4 as v4
from app import stage_b_resume_binding_v1 as stage_b_binding
from app.phase67_common import sha256_json, tokens

SCHEMA_VERSION = "native-resume-studio-service-v5-stage-b-bound"
SUBMISSION_AUTHORITY = False

_FORBIDDEN_EXACT_TYPES = frozenset(
    {
        "TOOL",
        "CERTIFICATION",
        "CLEARANCE",
        "CITIZENSHIP",
        "SPONSORSHIP",
        "WORK_AUTHORIZATION",
        "LICENSE",
        "LANGUAGE",
    }
)


def ensure_schema(connection=None) -> None:
    v4.ensure_schema(connection)
    planner.ensure_schema(connection)
    claim_trace.ensure_schema(connection)
    stage_b_binding.ensure_schema(connection)


def native_resume_authority_enabled() -> bool:
    return False


def _assert_plan_matches_inputs(
    *,
    plan: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    job_snapshot_sha256: str,
) -> None:
    if str(plan.get("job_snapshot_sha256") or "") != str(job_snapshot_sha256):
        raise RuntimeError("Stage B plan is bound to a different job snapshot.")
    binding = plan.get("candidate_truth_binding")
    if not isinstance(binding, Mapping):
        raise RuntimeError("Stage B plan is missing its Candidate Truth binding.")
    for key in ("source_extraction_id", "profile_revision", "profile_digest"):
        if str(binding.get(key) or "") != str(snapshot.get(key) or ""):
            raise RuntimeError("Stage B plan is bound to a different Candidate Truth state.")


def _stage_b_claim_guard(
    *,
    document: Any,
    bundle: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject unsupported JD-only language before any resume version is persisted.

    V4 already rejects unknown evidence IDs and unsupported numbers. This V5 guard
    adds the complementary Stage B check: language belonging to a do-not-claim JD
    requirement may not be introduced unless that exact claim's cited candidate
    evidence independently contains the same meaningful language.
    """
    evidence = v4.v1._evidence_map(dict(bundle))
    claims = claim_trace._resume_claims({"document": document.model_dump()})
    forbidden_refs = [
        ref
        for ref in plan.get("requirement_refs") or []
        if isinstance(ref, Mapping)
        and str(ref.get("requirement_id") or "")
        in set(plan.get("do_not_claim_requirement_ids") or [])
    ]
    violations: list[dict[str, Any]] = []
    for claim in claims:
        claim_text = str(claim.get("text") or "")
        claim_tokens = tokens(claim_text)
        support_text = v4.v1._support_text(list(claim.get("evidence_ids") or []), evidence)
        support_tokens = tokens(support_text)
        for ref in forbidden_refs:
            requirement_text = str(ref.get("exact_text") or "")
            requirement_tokens = tokens(requirement_text)
            if not requirement_tokens:
                continue
            jd_only_tokens = requirement_tokens - support_tokens
            mentioned = sorted(claim_tokens & jd_only_tokens)
            exact_phrase = requirement_text.casefold() in claim_text.casefold()
            ratio = len(mentioned) / max(1, len(requirement_tokens))
            requirement_type = str(ref.get("type") or "")
            violates = exact_phrase or (
                requirement_type in _FORBIDDEN_EXACT_TYPES and bool(mentioned)
            ) or (len(mentioned) >= 2 and ratio >= 0.50)
            if violates:
                violations.append(
                    {
                        "claim_id": claim["claim_id"],
                        "requirement_id": str(ref["requirement_id"]),
                        "requirement_type": requirement_type,
                        "unsupported_terms": mentioned[:12],
                    }
                )
    if violations:
        details = ", ".join(
            f"{item['claim_id']}→{item['requirement_id']}"
            for item in violations[:8]
        )
        raise ValueError(
            "Generated resume contains unsupported JD-only language for do-not-claim requirements: "
            + details
        )
    return {
        "status": "PASS",
        "forbidden_requirements_checked": len(forbidden_refs),
        "claims_checked": len(claims),
        "violations": [],
    }


def _insert_claim_trace(connection, trace: Mapping[str, Any]) -> None:
    value = claim_trace.validate_trace(trace)
    owner = v4.v1.current_owner(connection)
    if value["tenant_id"] != owner.tenant_id or value["user_id"] != owner.user_id:
        raise ValueError("Claim trace owner does not match the active candidate.")
    binding = value["candidate_truth_binding"]
    connection.execute(
        """INSERT INTO jd_resume_claim_traces(
               trace_id,tenant_id,user_id,job_id,job_snapshot_sha256,
               plan_id,plan_digest,resume_version_id,rendered_resume_sha256,
               source_extraction_id,profile_revision,profile_digest,
               trace_version,trace_digest,trace_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            value["trace_id"],
            owner.tenant_id,
            owner.user_id,
            int(value["job_id"]),
            value["job_snapshot_sha256"],
            value["plan_id"],
            value["plan_digest"],
            value["resume_version_id"],
            value["rendered_resume_sha256"],
            binding["source_extraction_id"],
            int(binding["profile_revision"]),
            binding["profile_digest"],
            claim_trace.TRACE_VERSION,
            value["trace_digest"],
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        ),
    )


def get_version(version_id: str) -> dict[str, Any]:
    record = v4.get_version(version_id)
    binding = stage_b_binding.resume_stage_b_binding(version_id)
    record["stage_b_bound"] = bool(binding)
    record["stage_b_binding"] = {
        key: binding[key]
        for key in (
            "plan_id",
            "plan_digest",
            "jd_snapshot_id",
            "jd_snapshot_digest",
            "match_snapshot_id",
            "match_digest",
            "trace_id",
            "trace_digest",
            "writer_context_sha256",
            "binding_version",
        )
        if binding and key in binding
    }
    if binding:
        record["stage_b_trace"] = claim_trace.get_trace(str(binding["trace_id"]))
    else:
        record["stage_b_trace"] = None
    return record


def list_versions(*, job_id: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    records = v4.list_versions(job_id=job_id, limit=limit)
    for record in records:
        binding = stage_b_binding.resume_stage_b_binding(str(record["version_id"]))
        record["stage_b_bound"] = bool(binding)
        if binding:
            record["stage_b_plan_id"] = str(binding["plan_id"])
            record["stage_b_plan_digest"] = str(binding["plan_digest"])
            record["stage_b_trace_id"] = str(binding["trace_id"])
    return records


def generate_resume(
    *,
    job_id: int,
    instruction: str = "",
    rewrite_mode: str = "medium",
    parent_version_id: str | None = None,
    locked_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Generate one immutable V5 resume from current truth + exact Stage B plan."""
    resolved_job_id = int(job_id)

    # Build/persist Stage B first, then capture exact current inputs and prove the
    # plan still matches them before any model call.
    plan = planner.plan_for_job(resolved_job_id, persist=True)
    snapshot = v4.truth_binding.current_candidate_profile_snapshot()
    owned_job_snapshot = v4.safe_owned_job_snapshot(resolved_job_id)
    context = owned_job_snapshot["job"]
    job_snapshot_sha256 = str(owned_job_snapshot["job_snapshot_sha256"])
    _assert_plan_matches_inputs(
        plan=plan,
        snapshot=snapshot,
        job_snapshot_sha256=job_snapshot_sha256,
    )
    if not planner.plan_freshness(str(plan["plan_id"]))["fresh"]:
        raise RuntimeError("Stage B plan is stale before resume generation.")

    mode = str(rewrite_mode or "medium").strip().casefold()
    policy = v4.v2.rewrite_policy(mode)
    bundle = v4._truth_bound_evidence_bundle(snapshot)
    instruction_text = str(instruction or "").strip()
    if len(instruction_text) > v4.v1._MAX_INSTRUCTION_CHARS:
        raise ValueError(
            f"Revision instruction must be at most {v4.v1._MAX_INSTRUCTION_CHARS:,} characters."
        )
    locks = sorted(
        {
            str(value).strip().casefold()
            for value in (locked_sections or [])
            if str(value).strip()
        }
    )
    if any(value not in v4.v1._LOCKABLE for value in locks):
        raise ValueError("Unsupported locked resume section.")

    parent_document: v4.v1.ResumeDocument | None = None
    if parent_version_id:
        parent = get_version(parent_version_id)
        if int(parent["job_id"]) != resolved_job_id:
            raise ValueError("A revision must remain attached to the same job.")
        v4.truth_binding.assert_parent_truth_current(parent_version_id, snapshot)
        v4.job_binding.assert_parent_job_current(parent_version_id, job_snapshot_sha256)
        stage_b_binding.assert_parent_plan_current(parent_version_id, plan)
        parent_document = v4.v1.ResumeDocument.model_validate(parent["document"])

    writer_context = planner.writer_context(plan)
    writer_context_sha256 = sha256_json(writer_context)
    config = v4.v2._resolve_writer_config()
    prompt_payload: dict[str, Any] = {
        "task": "revise_resume" if parent_document else "generate_resume",
        "rewrite_mode": mode,
        "rewrite_policy": policy,
        "job": context,
        "job_snapshot_sha256": job_snapshot_sha256,
        "stage_b_resume_plan": writer_context,
        "candidate_truth_profile": v4._profile_prompt_context(snapshot),
        "evidence_bundle": {
            "source_id": bundle["source_id"],
            "source_label": bundle["source_label"],
            "evidence_digest": bundle["evidence_digest"],
            "items": bundle["items"],
        },
        "instruction": instruction_text
        or (
            "Tailor this resume to the selected job using the Stage B supported requirements and retention order. "
            "Never claim anything listed in stage_b_resume_plan.do_not_claim. "
            "Use only supplied candidate evidence IDs."
        ),
        "locked_sections": locks,
        "current_resume": parent_document.model_dump() if parent_document else None,
    }

    candidate_payload, response_id, model = v4.v2._call_openai_v2(
        prompt_payload=prompt_payload,
        config=config,
        rewrite_mode=mode,
    )
    calls_used = 1
    proposed = v4.v1._apply_locks(
        v4.v1.ResumeDocument.model_validate(candidate_payload), parent_document, locks
    )
    diagnostics = v4.v1.analyze_document(proposed, context, bundle)
    stage_b_guard = _stage_b_claim_guard(document=proposed, bundle=bundle, plan=plan)
    repair_payload: dict[str, Any] | None = None

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
            "Repair only these content-budget issues while preserving facts, evidence references, Stage B priorities, "
            "and all do-not-claim restrictions. Do not add new facts: "
            + ", ".join(diagnostics["content_budget_issues"])
        )
        candidate_payload, repair_response_id, model = v4.v2._call_openai_v2(
            prompt_payload=repair_payload,
            config=config,
            rewrite_mode=mode,
        )
        calls_used += 1
        proposed = v4.v1._apply_locks(
            v4.v1.ResumeDocument.model_validate(candidate_payload), parent_document, locks
        )
        diagnostics = v4.v1.analyze_document(proposed, context, bundle)
        stage_b_guard = _stage_b_claim_guard(document=proposed, bundle=bundle, plan=plan)
        if diagnostics["content_budget_issues"]:
            raise ValueError(
                "Generated resume still exceeds the ATS content budget after one repair pass."
            )
        response_id = repair_response_id or response_id

    generation_input_sha256 = v4.job_binding.generation_input_digest(
        {
            "initial_prompt": prompt_payload,
            "repair_prompt": repair_payload,
            "writer_config": {
                "model": model,
                "reasoning_effort": config["reasoning_effort"],
                "max_calls_per_generation": int(config["max_calls_per_generation"]),
                "rewrite_mode": mode,
            },
            "stage_b_plan_id": plan["plan_id"],
            "stage_b_plan_digest": plan["plan_digest"],
            "stage_b_writer_context_sha256": writer_context_sha256,
        }
    )

    # Re-check all mutable inputs after model work and immediately before the
    # transaction. Any truth/job/plan drift produces zero resume persistence.
    v4._assert_generation_inputs_still_current(
        job_id=resolved_job_id,
        original_snapshot=snapshot,
        job_snapshot_sha256=job_snapshot_sha256,
    )
    if not planner.plan_freshness(str(plan["plan_id"]))["fresh"]:
        raise RuntimeError("Stage B plan changed during resume generation. Regenerate from current intelligence.")
    _assert_plan_matches_inputs(
        plan=plan,
        snapshot=v4.truth_binding.current_candidate_profile_snapshot(),
        job_snapshot_sha256=str(v4.safe_owned_job_snapshot(resolved_job_id)["job_snapshot_sha256"]),
    )

    html = v4.v1.render_ats_html(proposed)
    html_sha256 = v4.v1._sha256_text(html)
    public_truth_binding = v4.truth_binding.public_binding_state(snapshot)

    diagnostics.update(
        {
            "schema_version": "native-resume-diagnostics-v5-stage-b-bound",
            "rewrite_mode": mode,
            "writer_model": model,
            "writer_reasoning_effort": config["reasoning_effort"],
            "writer_api_calls": calls_used,
            "writer_api_call_limit": int(config["max_calls_per_generation"]),
            "writer_key_source": config["key_source"],
            "candidate_truth_binding": public_truth_binding,
            "job_snapshot_sha256": job_snapshot_sha256,
            "generation_input_sha256": generation_input_sha256,
            "stage_b": {
                "plan_id": plan["plan_id"],
                "plan_digest": plan["plan_digest"],
                "jd_snapshot_id": plan["jd_snapshot_id"],
                "jd_snapshot_digest": plan["jd_snapshot_digest"],
                "match_snapshot_id": plan["match_snapshot_id"],
                "match_digest": plan["match_digest"],
                "writer_context_sha256": writer_context_sha256,
                "evidence_coverage_score": plan["diagnostics"].get("evidence_coverage_score"),
                "unsupported_must_have_requirement_ids": plan.get(
                    "unsupported_must_have_requirement_ids"
                ),
                "claim_guard": stage_b_guard,
            },
        }
    )

    connection = v4.v1.get_connection()
    try:
        ensure_schema(connection)
        owner = v4.v1.current_owner(connection)
        v4.v1._commit_schema_before_write(connection)
        connection.execute("BEGIN IMMEDIATE")
        version_number = v4.v1._next_version_number(
            connection, owner.tenant_id, owner.user_id, resolved_job_id
        )
        version_id = f"native-resume-{v4.v1.uuid4()}"

        synthetic_resume = {
            "version_id": version_id,
            "job_id": resolved_job_id,
            "html_sha256": html_sha256,
            "document": proposed.model_dump(),
            "candidate_truth_bound": True,
            "candidate_truth_binding": public_truth_binding,
            "job_snapshot_bound": True,
            "job_snapshot_binding": {
                "job_id": resolved_job_id,
                "job_snapshot_sha256": job_snapshot_sha256,
                "generation_input_sha256": generation_input_sha256,
            },
        }
        stage_b_trace = claim_trace.build_trace(plan=plan, resume=synthetic_resume)
        claim_trace.validate_trace(stage_b_trace)
        diagnostics["stage_b"]["trace_id"] = stage_b_trace["trace_id"]
        diagnostics["stage_b"]["trace_digest"] = stage_b_trace["trace_digest"]
        diagnostics["stage_b"]["jd_linked_claim_count"] = stage_b_trace["diagnostics"][
            "jd_linked_claim_count"
        ]
        diagnostics["stage_b"]["supported_but_unrepresented_requirement_ids"] = stage_b_trace[
            "supported_but_unrepresented_requirement_ids"
        ]

        connection.execute(
            """INSERT INTO native_resume_versions(
                version_id,tenant_id,user_id,job_id,source_id,parent_version_id,version_number,
                instruction,locked_sections_json,model_name,model_response_id,evidence_digest,
                document_json,diagnostics_json,html_sha256,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'VALIDATED')""",
            (
                version_id,
                owner.tenant_id,
                owner.user_id,
                resolved_job_id,
                bundle["source_id"],
                parent_version_id,
                version_number,
                instruction_text,
                json.dumps(locks),
                model,
                response_id or None,
                bundle["evidence_digest"],
                json.dumps(proposed.model_dump(), ensure_ascii=False, sort_keys=True),
                json.dumps(diagnostics, ensure_ascii=False, sort_keys=True),
                html_sha256,
            ),
        )
        v4.truth_binding.save_resume_truth_binding(
            connection, version_id=version_id, snapshot=snapshot
        )
        v4.job_binding.save_resume_job_binding(
            connection,
            version_id=version_id,
            job_id=resolved_job_id,
            job_snapshot_sha256=job_snapshot_sha256,
            generation_input_sha256=generation_input_sha256,
        )
        _insert_claim_trace(connection, stage_b_trace)
        stage_b_binding.save_binding(
            connection,
            version_id=version_id,
            plan=plan,
            trace=stage_b_trace,
            writer_context_sha256=writer_context_sha256,
        )
        connection.commit()
        return get_version(version_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


MODEL_OPTIONS = v4.MODEL_OPTIONS
REWRITE_PRESETS = v4.REWRITE_PRESETS
REWRITE_MODES = v4.REWRITE_MODES
REASONING_OPTIONS = v4.REASONING_OPTIONS
active_source = v4.active_source
build_evidence_bundle = v4.build_evidence_bundle
delete_personal_api_key = v4.delete_personal_api_key
extract_uploaded_source = v4.extract_uploaded_source
job_context = v4.job_context
resume_job_options = v4.resume_job_options
safe_filename = v4.safe_filename
save_confirmed_source = v4.save_confirmed_source
save_personal_api_key = v4.save_personal_api_key
save_writer_settings = v4.save_writer_settings
version_diff = v4.version_diff
version_docx = v4.version_docx
version_html = v4.version_html
version_pdf = v4.version_pdf
writer_status = v4.writer_status
