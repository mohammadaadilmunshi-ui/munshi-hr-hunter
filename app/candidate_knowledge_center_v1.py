"""Candidate Knowledge Center read/write model for the MUNSHI dashboard.

This surface exposes canonical knowledge and provenance, not application-question
wording. Normal scalar facts may be corrected once here and then reused by the
Universal Brain globally. Protected/self-ID/secret values remain reference-only
and are never rendered as plaintext by this module.
"""
from __future__ import annotations

from typing import Any, Mapping

from app import candidate_knowledge_graph_v1 as graph
from app import candidate_knowledge_ingestion_v1 as ingestion
from app import candidate_ontology_v1 as ontology

CENTER_VERSION = "candidate-knowledge-center-v1"

_SECTION_ORDER = (
    "Identity",
    "Education",
    "Employment",
    "Skills & tools",
    "Work authorization",
    "Career preferences",
    "Application behavior",
)


def _section(concept_id: str, category: str) -> str:
    key = str(concept_id or "").casefold()
    cat = str(category or "").upper()
    if key.startswith("contact."):
        return "Identity"
    if key.startswith("education.") or cat == "EDUCATION":
        return "Education"
    if key.startswith(("employment.", "experience.")) and not key.startswith("experience.skill."):
        return "Employment"
    if key.startswith("experience.skill.") or key.startswith("skills.") or cat in {"SKILLS", "EXPERIENCE"}:
        return "Skills & tools"
    if cat == "WORK_AUTHORIZATION" or key.startswith(("eligibility.", "work.authorization", "work.sponsorship")):
        return "Work authorization"
    if cat in {"WORK_PREFERENCE", "COMPENSATION", "AVAILABILITY", "LOCATION"} or key.startswith(
        ("preference.", "work.relocation", "compensation.", "availability.", "schedule.")
    ):
        return "Career preferences"
    return "Application behavior"


def _safe_fact(raw: Mapping[str, Any]) -> dict[str, Any]:
    row = dict(raw)
    concept_id = str(row.get("concept_id") or row.get("source_fact_key") or "").strip().casefold()
    item = ontology.concept(concept_id)
    sensitivity = str(row.get("sensitivity") or (item.sensitivity if item else "NORMAL")).upper()
    protected = sensitivity != "NORMAL"
    result = {
        "fact_id": row.get("fact_id") or row.get("source_fact_id"),
        "concept_id": concept_id,
        "label": concept_id.replace(".", " ").replace("_", " ").title(),
        "category": item.category if item else "PROFILE",
        "section": _section(concept_id, item.category if item else "PROFILE"),
        "value_type": row.get("value_type") or (item.value_type if item else "text"),
        "sensitivity": sensitivity,
        "protected": protected,
        "source_type": str(row.get("source_type") or "CANDIDATE_TRUTH"),
        "source_ref": str(row.get("source_ref") or ""),
        "trust_level": str(row.get("trust_level") or "UNKNOWN"),
        "confidence": float(row.get("confidence") or 0.0),
        "autofill_policy": str(row.get("autofill_policy") or "REQUIRE_CONFIRMATION"),
        "revision": int(row.get("revision") or row.get("candidate_profile_revision") or 0),
        "editable": bool(item is not None and sensitivity == "NORMAL" and item.answer_mode in {"DIRECT_FACT", "PREFERENCE"}),
    }
    if protected:
        result["value"] = None
        result["value_reference_available"] = bool(row.get("value_ref"))
    else:
        result["value"] = row.get("value")
        result["value_reference_available"] = False
    return result


def snapshot() -> dict[str, Any]:
    try:
        view = ingestion.read_only_knowledge_view()
    except (LookupError, RuntimeError, ValueError) as error:
        return {
            "version": CENTER_VERSION,
            "ready": False,
            "reason": str(error),
            "sections": {name: [] for name in _SECTION_ORDER},
            "conflicts": [],
            "knowledge_revision": graph.current_revision(),
        }

    # Choose the highest-authority visible row per concept for the primary view;
    # provenance remains attached and all durable conflicts are surfaced separately.
    safe_rows = [_safe_fact(row) for row in view.get("facts") or [] if isinstance(row, Mapping)]
    priority = graph.SOURCE_PRECEDENCE
    trust = graph.TRUST_RANK
    safe_rows.sort(
        key=lambda row: (
            priority.get(str(row.get("source_type") or "").upper(), 0),
            trust.get(str(row.get("trust_level") or "UNKNOWN").upper(), 0),
            float(row.get("confidence") or 0.0),
            int(row.get("revision") or 0),
        ),
        reverse=True,
    )
    primary: dict[str, dict[str, Any]] = {}
    for row in safe_rows:
        key = str(row.get("concept_id") or "")
        if key and key not in primary:
            primary[key] = row

    claims = [dict(row) for row in view.get("claims") or [] if isinstance(row, Mapping)]
    for claim in claims:
        concept_id = str(claim.get("concept") or "").strip().casefold()
        if not concept_id or concept_id in primary:
            continue
        item = ontology.concept(concept_id)
        primary[concept_id] = {
            "fact_id": claim.get("claim_id"),
            "concept_id": concept_id,
            "label": concept_id.replace(".", " ").replace("_", " ").title(),
            "category": item.category if item else "EXPERIENCE",
            "section": "Skills & tools",
            "value_type": "boolean",
            "sensitivity": "NORMAL",
            "protected": False,
            "value": True,
            "source_type": "RESUME",
            "source_ref": str(claim.get("source_ref") or ""),
            "trust_level": "SOURCE_CONFIRMED",
            "confidence": float(claim.get("confidence") or 0.0),
            "autofill_policy": "ALLOW",
            "revision": int(view.get("profile_revision") or 0),
            "editable": False,
            "evidence_text": str(claim.get("evidence_text") or ""),
            "entity_ref": claim.get("entity_ref"),
        }

    sections = {name: [] for name in _SECTION_ORDER}
    for row in sorted(primary.values(), key=lambda item: (str(item["section"]), str(item["concept_id"]))):
        sections.setdefault(str(row["section"]), []).append(row)

    try:
        conflicts = graph.open_conflicts()
    except (LookupError, RuntimeError, ValueError):
        conflicts = []
    return {
        "version": CENTER_VERSION,
        "ready": True,
        "profile_revision": int(view.get("profile_revision") or 0),
        "profile_digest": str(view.get("profile_digest") or ""),
        "knowledge_revision": graph.current_revision(),
        "persisted_graph_fact_count": int(view.get("persisted_graph_fact_count") or 0),
        "fact_count": len(primary),
        "claim_count": len(claims),
        "sections": sections,
        "conflicts": conflicts,
    }


def save_normal_fact(
    *,
    concept_id: str,
    value: Any,
    source_ref: str = "dashboard:knowledge-center",
) -> dict[str, Any]:
    item = ontology.require_concept(concept_id)
    if item.sensitivity != "NORMAL":
        raise ValueError("Protected or sensitive facts must use their dedicated policy/store.")
    if item.answer_mode not in {"DIRECT_FACT", "PREFERENCE"}:
        raise ValueError("Only direct facts and explicit preferences are editable here.")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("Candidate Knowledge value cannot be blank.")
    return graph.store_fact(
        concept_id=item.concept_id,
        value=value,
        value_type=item.value_type,
        source_type="USER_DASHBOARD",
        source_ref=f"{source_ref}:{item.concept_id}",
        trust_level="USER_CONFIRMED",
        confidence=1.0,
        sensitivity="NORMAL",
        autofill_policy="ALLOW" if item.autofill_default else "REQUIRE_CONFIRMATION",
    )
