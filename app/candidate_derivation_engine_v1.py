"""Evidence-backed deterministic derivations for MUNSHI Universal Brain."""
from __future__ import annotations

import math
import re
import sqlite3
from datetime import date, datetime
from typing import Any, Mapping

from dateutil import parser as date_parser

from app import candidate_knowledge_graph_v1 as graph
from app import candidate_ontology_v1 as ontology
from app.database import get_connection

DERIVATION_VERSION = "candidate-derivation-engine-v1"

_CURRENT_TOKENS = {"present", "current", "now", "ongoing", "today"}


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _month_index(value: Any, *, as_of: date, current_if_blank: bool = False) -> int | None:
    text = str(value or "").strip()
    normalized = _normalize(text)
    if not normalized:
        if not current_if_blank:
            return None
        return as_of.year * 12 + (as_of.month - 1)
    if normalized in _CURRENT_TOKENS or any(token in normalized for token in ("present", "current")):
        return as_of.year * 12 + (as_of.month - 1)
    # Preserve exact YYYY-MM and YYYY first; dateutil fills missing components
    # from today's date which would accidentally increase duration.
    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", normalized)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12:
            return year * 12 + (month - 1)
        return None
    match = re.fullmatch(r"(\d{4})", normalized)
    if match:
        return int(match.group(1)) * 12  # January: conservative for an end year too.
    try:
        parsed = date_parser.parse(text, fuzzy=True, default=datetime(1900, 1, 1))
    except (ValueError, OverflowError, TypeError):
        return None
    if parsed.year < 1900 or parsed.year > as_of.year + 20:
        return None
    return parsed.year * 12 + (parsed.month - 1)


def _employment_intervals(*, connection: sqlite3.Connection, as_of: date) -> list[dict[str, Any]]:
    starts = graph.current_facts("employment.start_date", connection=connection)
    ends = graph.current_facts("employment.end_date", connection=connection)
    end_by_entity = {str(row.get("entity_ref") or ""): row for row in ends}
    intervals: list[dict[str, Any]] = []
    for start_fact in starts:
        entity = str(start_fact.get("entity_ref") or "")
        if not entity:
            continue
        start = _month_index(start_fact.get("value"), as_of=as_of)
        if start is None:
            continue
        end_fact = end_by_entity.get(entity)
        # Existing Candidate Truth treats experience.0 as current employer/title.
        current_if_blank = entity == "experience.0"
        end = _month_index(end_fact.get("value") if end_fact else None, as_of=as_of, current_if_blank=current_if_blank)
        if end is None or end <= start:
            continue
        intervals.append(
            {
                "entity_ref": entity,
                "start_month": start,
                "end_month": min(end, as_of.year * 12 + (as_of.month - 1)),
                "source_fact_ids": [start_fact["fact_id"], *(([end_fact["fact_id"]]) if end_fact else [])],
            }
        )
    return intervals


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    if not ordered:
        return []
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        previous_start, previous_end = merged[-1]
        if start <= previous_end:
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _duration_result(intervals: list[dict[str, Any]], *, concept_id: str) -> dict[str, Any]:
    merged = _merge_intervals([(int(item["start_month"]), int(item["end_month"])) for item in intervals])
    months = sum(end - start for start, end in merged)
    if months <= 0:
        return {"status": "MISSING_FACT", "concept_id": concept_id, "derivation_version": DERIVATION_VERSION}
    # Floor, never round upward. A display layer can choose integer or decimal.
    years_decimal = math.floor((months / 12.0) * 100.0) / 100.0
    return {
        "status": "DERIVED",
        "concept_id": concept_id,
        "answer_mode": "COMPUTED_FACT",
        "value": years_decimal,
        "value_type": "number",
        "months": months,
        "years_floor": months // 12,
        "source_fact_ids": sorted({fid for item in intervals for fid in item["source_fact_ids"]}),
        "evidence_ids": [],
        "confidence": 0.98,
        "reason": "OVERLAP_AWARE_EMPLOYMENT_DURATION",
        "derivation_version": DERIVATION_VERSION,
        "rounding_policy": "FLOOR_NEVER_UP",
    }


def derive_total_experience(*, as_of: date | None = None, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        intervals = _employment_intervals(connection=connection, as_of=as_of or date.today())
        return _duration_result(intervals, concept_id="experience.total_years")
    finally:
        if owns:
            connection.close()


def _skill_concept(skill: str) -> str | None:
    raw = str(skill or "").strip()
    if not raw:
        return None
    direct = "skill." + re.sub(r"[^a-z0-9]+", "_", raw.casefold()).strip("_")
    if ontology.concept(direct):
        return direct
    candidates = ontology.retrieve(raw, top_k=5)
    for item in candidates:
        if str(item["concept_id"]).startswith("skill.") and float(item["score"]) >= 0.82:
            return str(item["concept_id"])
    return None


def derive_capability_boolean(
    concept_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    concept = ontology.require_concept(concept_id)
    if not concept.concept_id.startswith("skill."):
        raise ValueError("Capability derivation requires a skill.* concept.")
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        relations = graph.current_relations(predicate="HAS_SKILL", object_concept=concept.concept_id, connection=connection)
        if not relations:
            # Absence of evidence is not evidence of absence; never invent "No".
            return {
                "status": "MISSING_FACT",
                "concept_id": concept.concept_id,
                "reason": "NO_EXPLICIT_CAPABILITY_EVIDENCE",
                "derivation_version": DERIVATION_VERSION,
            }
        evidence_ids = sorted({eid for relation in relations for eid in relation.get("evidence_ids") or []})
        return {
            "status": "DERIVED",
            "concept_id": concept.concept_id,
            "answer_mode": "EXPERIENCE_EVIDENCE_BOOLEAN",
            "value": True,
            "value_type": "boolean",
            "source_fact_ids": [],
            "evidence_ids": evidence_ids,
            "confidence": min(float(relation.get("confidence") or 0.0) for relation in relations),
            "reason": "EXPLICIT_CAPABILITY_EVIDENCE",
            "derivation_version": DERIVATION_VERSION,
        }
    finally:
        if owns:
            connection.close()


def derive_skill_years(
    skill: str,
    *,
    as_of: date | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    skill_concept = _skill_concept(skill)
    if skill_concept is None:
        return {"status": "MISSING_FACT", "concept_id": "experience.skill_years", "reason": "UNKNOWN_SKILL_CONCEPT", "derivation_version": DERIVATION_VERSION}
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        relations = graph.current_relations(predicate="HAS_SKILL", object_concept=skill_concept, connection=connection)
        if not relations:
            return {"status": "MISSING_FACT", "concept_id": "experience.skill_years", "reason": "NO_EXPLICIT_SKILL_EVIDENCE", "derivation_version": DERIVATION_VERSION}
        entities = {str(row["subject_concept"]) for row in relations if str(row.get("subject_concept") or "").startswith("experience.")}
        if not entities:
            # Skill-section evidence proves usage but not duration.
            return {"status": "MISSING_FACT", "concept_id": "experience.skill_years", "reason": "SKILL_DURATION_NOT_EVIDENCED_BY_DATED_EXPERIENCE", "derivation_version": DERIVATION_VERSION}
        all_intervals = _employment_intervals(connection=connection, as_of=as_of or date.today())
        intervals = [item for item in all_intervals if item["entity_ref"] in entities]
        result = _duration_result(intervals, concept_id="experience.skill_years")
        if result.get("status") == "DERIVED":
            result["conditions"] = {"skill": skill}
            result["evidence_ids"] = sorted({eid for row in relations for eid in row.get("evidence_ids") or []})
            result["reason"] = "DATED_EXPERIENCE_WITH_EXPLICIT_SKILL_EVIDENCE"
        return result
    finally:
        if owns:
            connection.close()


def derive_work_mode(
    concept_id: str,
    *,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    wanted = {
        "work.remote": {"remote", "work from home", "wfh"},
        "work.hybrid": {"hybrid"},
        "work.onsite": {"onsite", "on site", "in person", "in-person"},
    }.get(concept_id)
    if wanted is None:
        raise ValueError("Unsupported work-mode concept.")
    owns = connection is None
    connection = connection or get_connection()
    try:
        graph.ensure_schema(connection)
        resolved = graph.resolve_fact("preference.work_mode", connection=connection)
        if resolved.get("status") != "ANSWERED":
            return {"status": "MISSING_FACT", "concept_id": concept_id, "reason": "EXPLICIT_WORK_MODE_PREFERENCE_MISSING", "derivation_version": DERIVATION_VERSION}
        fact = dict(resolved["fact"])
        raw = fact.get("value")
        values = raw if isinstance(raw, list) else [raw]
        normalized = {_normalize(item).replace("-", " ") for item in values if str(item or "").strip()}
        supported = any(any(token == value or token in value for token in wanted) for value in normalized)
        return {
            "status": "DERIVED",
            "concept_id": concept_id,
            "answer_mode": "PREFERENCE",
            "value": bool(supported),
            "value_type": "boolean",
            "source_fact_ids": [fact["fact_id"]],
            "evidence_ids": [],
            "confidence": float(fact.get("confidence") or 0.0),
            "reason": "EXPLICIT_WORK_MODE_PREFERENCE",
            "derivation_version": DERIVATION_VERSION,
        }
    finally:
        if owns:
            connection.close()


def derive(
    concept_id: str,
    *,
    conditions: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    key = str(concept_id or "").strip().casefold()
    conditions = dict(conditions or {})
    if key == "experience.total_years":
        return derive_total_experience(as_of=as_of, connection=connection)
    if key == "experience.skill_years":
        return derive_skill_years(str(conditions.get("skill") or ""), as_of=as_of, connection=connection)
    if key.startswith("skill."):
        return derive_capability_boolean(key, connection=connection)
    if key in {"work.remote", "work.hybrid", "work.onsite"}:
        return derive_work_mode(key, connection=connection)
    return {"status": "MISSING_FACT", "concept_id": key, "reason": "NO_SAFE_DERIVATION", "derivation_version": DERIVATION_VERSION}
