"""Evidence-backed derivation engine for MUNSHI Universal Brain.

Derivations are deterministic and conservative.  The engine never upgrades an
unsupported resume claim into candidate truth: capabilities are emitted only
when explicit skill text or exact source evidence supports them, and experience
durations never round upward.
"""
from __future__ import annotations

import calendar
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

DERIVATION_VERSION = "candidate-derivation-engine-v1"

# Provider-neutral capability ontology. Patterns require explicit source text;
# they are not role/title assumptions.
_CAPABILITY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "recruiting": (re.compile(r"\brecruit(?:ing|ment|er|ed)?\b", re.I), re.compile(r"\bhiring\b", re.I)),
    "technical_recruiting": (re.compile(r"\btechnical recruit(?:ing|ment|er)?\b", re.I),),
    "candidate_sourcing": (re.compile(r"\b(candidate )?sourc(?:ing|ed|e)\b", re.I),),
    "resume_screening": (re.compile(r"\b(resume|cv) screen(?:ing|ed)?\b", re.I), re.compile(r"\bscreen(?:ing|ed) candidates?\b", re.I)),
    "interviewing": (re.compile(r"\binterview(?:ing|ed|s)?\b", re.I),),
    "onboarding": (re.compile(r"\bon[- ]?board(?:ing|ed)?\b", re.I),),
    "i9": (re.compile(r"\bi[- ]?9\b", re.I),),
    "documentation": (re.compile(r"\bdocument(?:ation|ing|ed)?\b", re.I),),
    "hr_operations": (re.compile(r"\bhr operations?\b", re.I), re.compile(r"\bhuman resources operations?\b", re.I)),
    "employee_records": (re.compile(r"\bemployee records?\b", re.I),),
    "compliance": (re.compile(r"\bcompliance\b", re.I),),
    "audit_support": (re.compile(r"\baudit(?:s|ing)?\b", re.I),),
    "training_coordination": (re.compile(r"\btraining (?:coordination|coordinat(?:e|ed|ing))\b", re.I),),
    "payroll_support": (re.compile(r"\bpayroll\b", re.I),),
    "timekeeping": (re.compile(r"\btime[- ]?keeping\b", re.I), re.compile(r"\btimesheets?\b", re.I)),
    "analytics": (re.compile(r"\banalytics?\b", re.I), re.compile(r"\banalys(?:is|es|t)\b", re.I)),
    "dashboarding": (re.compile(r"\bdashboards?\b", re.I),),
    "data_cleaning": (re.compile(r"\bdata clean(?:ing|ed)?\b", re.I), re.compile(r"\bclean(?:ing|ed) data\b", re.I)),
    "excel": (re.compile(r"\b(?:microsoft )?excel\b", re.I),),
    "power_bi": (re.compile(r"\bpower\s*bi\b", re.I),),
    "workday": (re.compile(r"\bworkday\b", re.I),),
    "greenhouse": (re.compile(r"\bgreenhouse\b", re.I),),
    "bamboohr": (re.compile(r"\bbamboo\s*hr\b", re.I),),
    "successfactors": (re.compile(r"\bsuccess\s*factors\b", re.I),),
    "adp": (re.compile(r"\badp(?: workforce now)?\b", re.I),),
    "ukg": (re.compile(r"\bukg(?: pro)?\b", re.I), re.compile(r"\bultimate kronos\b", re.I)),
    "python": (re.compile(r"\bpython\b", re.I),),
    "tableau": (re.compile(r"\btableau\b", re.I),),
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _clean(value).casefold()).strip("_")[:100]


def capability_for_text(text: str) -> tuple[str, ...]:
    raw = _clean(text)
    if not raw:
        return ()
    return tuple(
        key for key, patterns in _CAPABILITY_PATTERNS.items()
        if any(pattern.search(raw) for pattern in patterns)
    )


def capability_for_skill(skill: str) -> tuple[str, ...]:
    """Map an explicit skill label to capabilities; no role/context inference."""
    raw = _clean(skill)
    if not raw:
        return ()
    return capability_for_text(raw)


def _claim_id(source_ref: str, concept: str) -> str:
    digest = hashlib.sha256(f"{source_ref}\n{concept}".encode("utf-8")).hexdigest()
    return f"claim-{digest[:32]}"


def extract_profile_claims(
    profile: Mapping[str, Any],
    *,
    extraction_id: str = "",
    source_digest: str = "",
) -> list[dict[str, Any]]:
    """Extract atomic, source-linked capability claims from explicit profile text."""
    claims: list[dict[str, Any]] = []
    base_ref = f"master-resume-extraction:{extraction_id}" if extraction_id else "candidate-profile"

    def add(capability: str, *, entity_ref: str, evidence_ref: str, evidence_text: str, confidence: float) -> None:
        concept = f"experience.skill.{capability}"
        source_ref = f"{base_ref}/{evidence_ref}"
        claims.append(
            {
                "claim_id": _claim_id(source_ref, concept),
                "claim_type": "EXPERIENCE_CAPABILITY",
                "concept": concept,
                "capability": capability,
                "entity_ref": entity_ref,
                "evidence_text_ref": evidence_ref,
                "evidence_text": _clean(evidence_text),
                "source_ref": source_ref,
                "source_digest": source_digest or None,
                "confidence": float(confidence),
            }
        )

    for i, group in enumerate(profile.get("skills") or []):
        if not isinstance(group, Mapping):
            continue
        for j, raw_skill in enumerate(group.get("skills") or []):
            skill = _clean(raw_skill)
            capabilities = capability_for_skill(skill)
            if not capabilities and skill:
                capabilities = (_slug(skill),)
            for capability in capabilities:
                add(
                    capability,
                    entity_ref=f"skills.{i}",
                    evidence_ref=f"skills.{i}.skills.{j}",
                    evidence_text=skill,
                    confidence=0.99,
                )

    for i, raw_exp in enumerate(profile.get("experience") or []):
        if not isinstance(raw_exp, Mapping):
            continue
        for j, raw_bullet in enumerate(raw_exp.get("bullets") or []):
            bullet = _clean(raw_bullet)
            for capability in capability_for_text(bullet):
                add(
                    capability,
                    entity_ref=f"experience.{i}",
                    evidence_ref=f"experience.{i}.bullets.{j}",
                    evidence_text=bullet,
                    confidence=0.98,
                )

    for i, raw_project in enumerate(profile.get("projects") or []):
        if not isinstance(raw_project, Mapping):
            continue
        for j, raw_tool in enumerate(raw_project.get("tools") or []):
            tool = _clean(raw_tool)
            capabilities = capability_for_skill(tool)
            if not capabilities and tool:
                capabilities = (_slug(tool),)
            for capability in capabilities:
                add(
                    capability,
                    entity_ref=f"projects.{i}",
                    evidence_ref=f"projects.{i}.tools.{j}",
                    evidence_text=tool,
                    confidence=0.97,
                )
        for j, raw_bullet in enumerate(raw_project.get("bullets") or []):
            bullet = _clean(raw_bullet)
            for capability in capability_for_text(bullet):
                add(
                    capability,
                    entity_ref=f"projects.{i}",
                    evidence_ref=f"projects.{i}.bullets.{j}",
                    evidence_text=bullet,
                    confidence=0.95,
                )

    # Deterministic de-duplication by source-linked claim identity.
    unique: dict[str, dict[str, Any]] = {}
    for claim in claims:
        unique[str(claim["claim_id"])] = claim
    return [unique[key] for key in sorted(unique)]


def _month_date(year: int, month: int, *, end: bool) -> date:
    day = calendar.monthrange(year, month)[1] if end else 1
    return date(year, month, day)


def parse_date(value: Any, *, end: bool = False, as_of: date | None = None) -> date | None:
    text = _clean(value).casefold().strip(".,")
    if not text:
        return None
    if text in {"present", "current", "now", "ongoing"}:
        return as_of or date.today()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%Y", "%Y-%m"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            if fmt in {"%m/%Y", "%Y-%m"}:
                return _month_date(parsed.year, parsed.month, end=end)
            return parsed
        except ValueError:
            pass
    match = re.fullmatch(r"(?P<month>[a-z]+)\s+(?P<year>\d{4})", text)
    if match and match.group("month") in _MONTHS:
        return _month_date(int(match.group("year")), _MONTHS[match.group("month")], end=end)
    match = re.fullmatch(r"(?P<year>\d{4})", text)
    if match:
        year = int(match.group("year"))
        return date(year, 12, 31) if end else date(year, 1, 1)
    return None


def experience_intervals(
    profile: Mapping[str, Any],
    *,
    as_of: date | None = None,
    entity_refs: set[str] | None = None,
) -> list[tuple[date, date, str]]:
    current = as_of or date.today()
    rows: list[tuple[date, date, str]] = []
    for index, raw in enumerate(profile.get("experience") or []):
        if not isinstance(raw, Mapping):
            continue
        entity = f"experience.{index}"
        if entity_refs is not None and entity not in entity_refs:
            continue
        start = parse_date(raw.get("start_date"), end=False, as_of=current)
        end = parse_date(raw.get("end_date"), end=True, as_of=current)
        if start is None:
            continue
        if end is None:
            # Missing end date is not automatically "present". Only explicit
            # present/current text may resolve to as_of.
            continue
        if end > current:
            end = current
        if end < start:
            continue
        rows.append((start, end, entity))
    return rows


def merge_intervals(intervals: Sequence[tuple[date, date, str]]) -> list[tuple[date, date]]:
    ordered = sorted((start, end) for start, end, _entity in intervals)
    if not ordered:
        return []
    merged: list[tuple[date, date]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            if end > prev_end:
                merged[-1] = (prev_start, end)
        else:
            merged.append((start, end))
    return merged


def conservative_years(intervals: Sequence[tuple[date, date, str]]) -> float:
    merged = merge_intervals(intervals)
    days = sum((end - start).days + 1 for start, end in merged)
    exact = days / 365.2425
    # Never round upward.  Two decimals are enough for downstream range/select
    # conversion while retaining a conservative derivation.
    return math.floor(exact * 100.0) / 100.0


def total_experience(profile: Mapping[str, Any], *, as_of: date | None = None) -> dict[str, Any]:
    intervals = experience_intervals(profile, as_of=as_of)
    if not intervals:
        return {"status": "MISSING_FACT", "reason": "no_complete_experience_intervals"}
    merged = merge_intervals(intervals)
    return {
        "status": "DERIVED",
        "answer_mode": "COMPUTED_FACT",
        "value": conservative_years(intervals),
        "unit": "years",
        "evidence_entities": [entity for _start, _end, entity in intervals],
        "merged_intervals": [(start.isoformat(), end.isoformat()) for start, end in merged],
        "derivation_version": DERIVATION_VERSION,
    }


def capability_evidence(
    capability: str,
    claims: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    wanted = _slug(capability)
    return [dict(claim) for claim in claims if _slug(str(claim.get("capability") or "")) == wanted]


def capability_boolean(capability: str, claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evidence = capability_evidence(capability, claims)
    if not evidence:
        return {"status": "MISSING_FACT", "reason": "no_explicit_capability_evidence"}
    return {
        "status": "DERIVED",
        "answer_mode": "EXPERIENCE_EVIDENCE_BOOLEAN",
        "value": True,
        "capability": _slug(capability),
        "evidence_ids": [str(item.get("claim_id")) for item in evidence],
        "derivation_version": DERIVATION_VERSION,
    }


def capability_years(
    capability: str,
    profile: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    evidence = capability_evidence(capability, claims)
    entity_refs = {
        str(item.get("entity_ref")) for item in evidence
        if str(item.get("entity_ref") or "").startswith("experience.")
    }
    if not entity_refs:
        return {"status": "MISSING_FACT", "reason": "capability_not_bound_to_dated_experience"}
    intervals = experience_intervals(profile, as_of=as_of, entity_refs=entity_refs)
    if not intervals:
        return {"status": "MISSING_FACT", "reason": "capability_experience_dates_missing"}
    return {
        "status": "DERIVED",
        "answer_mode": "COMPUTED_FACT",
        "value": conservative_years(intervals),
        "unit": "years",
        "capability": _slug(capability),
        "evidence_ids": [str(item.get("claim_id")) for item in evidence],
        "evidence_entities": sorted(entity_refs),
        "derivation_version": DERIVATION_VERSION,
    }


def supported_capabilities(claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = sorted({str(item.get("capability")) for item in claims if item.get("capability")})
    if not values:
        return {"status": "MISSING_FACT", "reason": "no_explicit_capability_evidence"}
    return {
        "status": "DERIVED",
        "answer_mode": "ENUMERATION",
        "value": values,
        "evidence_ids": sorted({str(item.get("claim_id")) for item in claims if item.get("claim_id")}),
        "derivation_version": DERIVATION_VERSION,
    }
