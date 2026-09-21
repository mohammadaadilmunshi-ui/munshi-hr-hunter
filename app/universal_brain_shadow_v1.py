"""Value-free shadow comparison between legacy application resolution and Universal Brain."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

from app import answer_brain_v2
from app import candidate_knowledge_graph_v1 as graph
from app import universal_answer_resolver_v1 as answers
from app import universal_question_resolver_v1 as questions

SHADOW_VERSION = "universal-brain-shadow-v1"
_LOG = logging.getLogger(__name__)


def _current_answer_state(requirement: Mapping[str, Any]) -> str:
    try:
        resolved = answer_brain_v2.resolve_answer(
            question_family=str(requirement.get("question_family") or "unknown"),
            conditions=dict(requirement.get("conditions") or {}),
            profile_fact_key=requirement.get("profile_fact_key"),
            question_key=str(requirement.get("question_key") or "") or None,
        )
    except (LookupError, RuntimeError, ValueError):
        return "UNAVAILABLE"
    return str(resolved.get("status") or "UNKNOWN")


def compare(
    observation: Mapping[str, Any],
    *,
    current_requirement: Mapping[str, Any] | None = None,
    knowledge_view: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    question = questions.resolve_question(observation)
    answer = answers.resolve_concept(question, knowledge_view=knowledge_view)
    trace = answers.public_trace(answer)
    current = dict(current_requirement or {})
    result = {
        "shadow_version": SHADOW_VERSION,
        "enabled": graph.universal_brain_shadow_enabled(),
        "current_resolver": {
            "question_key": current.get("question_key"),
            "family": current.get("question_family"),
            "sensitivity": current.get("sensitivity_class"),
            "answer_state": _current_answer_state(current) if current else None,
        },
        "universal_brain": {
            "concept": trace.get("concept_id"),
            "classification_lane": dict(trace.get("question_resolution") or {}).get("classification_lane"),
            "concept_confidence": dict(trace.get("question_resolution") or {}).get("confidence"),
            "answer_status": trace.get("answer_status"),
            "answer_mode": trace.get("answer_mode"),
            "answer_source": trace.get("answer_source"),
            "evidence_count": len(trace.get("evidence_ids") or []),
            "would_autofill": bool(trace.get("autofill_allowed")),
            "sensitivity": trace.get("sensitivity"),
            "reason": trace.get("reason"),
            "knowledge_revision": trace.get("knowledge_revision"),
            "value_exposed_in_trace": False,
        },
    }
    result["semantic_improvement"] = bool(
        str(current.get("question_key") or "").startswith("unknown.")
        and result["universal_brain"]["concept"]
    )
    result["sensitivity_downgraded"] = False
    if graph.universal_brain_shadow_enabled():
        _LOG.info("MUNSHI_UNIVERSAL_BRAIN_SHADOW %s", json.dumps(result, sort_keys=True, separators=(",", ":")))
    return result
