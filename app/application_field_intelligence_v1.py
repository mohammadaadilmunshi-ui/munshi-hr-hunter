"""Deterministic field-intelligence for application forms.

This module converts common provider field metadata (label/name/autocomplete/type)
into stable semantic requirements and can project safe values from the confirmed
Candidate Truth snapshot.  It is deliberately provider-neutral and contains no
candidate-specific values.

The intended execution order is:

1. deterministic field semantics + Candidate Truth
2. canonical Application Answer Vault
3. NEEDS_INPUT for unresolved factual questions
4. bounded AI only to understand unusual page/control wording, never to invent facts

MUNSHI Apply and AutoApply can therefore share one factual authority even when a
provider asks for "Full name", "First name", "Given name", "Surname", etc.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from app import phase45_truth_binding as truth_binding

INTELLIGENCE_VERSION = "application-field-intelligence-v1"


@dataclass(frozen=True)
class FieldIntent:
    key: str
    profile_fact_key: str
    projection: str
    answer_type: str = "text"
    confidence: float = 1.0


_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

_AUTOCOMPLETE_MAP: dict[str, FieldIntent] = {
    "name": FieldIntent("contact.full_name", "contact.full_name", "identity"),
    "given-name": FieldIntent("contact.first_name", "contact.full_name", "first_name"),
    "additional-name": FieldIntent("contact.middle_name", "contact.full_name", "middle_name"),
    "family-name": FieldIntent("contact.last_name", "contact.full_name", "last_name"),
    "email": FieldIntent("contact.email", "contact.email", "identity", "email"),
    "tel": FieldIntent("contact.phone", "contact.phone", "identity", "phone"),
    "tel-national": FieldIntent("contact.phone", "contact.phone", "identity", "phone"),
    "organization": FieldIntent(
        "employment.current_employer", "experience.0.employer", "identity"
    ),
    "organization-title": FieldIntent(
        "employment.current_title", "experience.0.title", "identity"
    ),
    "address-level2": FieldIntent("contact.city", "contact.location", "location_city"),
    "address-level1": FieldIntent("contact.region", "contact.location", "location_region"),
    "postal-code": FieldIntent("contact.postal_code", "contact.location", "location_postal"),
    "country": FieldIntent("contact.country", "contact.location", "location_country"),
    "country-name": FieldIntent("contact.country", "contact.location", "location_country"),
}

_LABEL_RULES: tuple[tuple[re.Pattern[str], FieldIntent], ...] = (
    (re.compile(r"\b(first|given)\s+name\b", re.I), _AUTOCOMPLETE_MAP["given-name"]),
    (re.compile(r"\b(middle|additional)\s+name\b", re.I), _AUTOCOMPLETE_MAP["additional-name"]),
    (re.compile(r"\b(last|family|sur)\s*name\b", re.I), _AUTOCOMPLETE_MAP["family-name"]),
    (
        re.compile(r"\b(full|legal|preferred)?\s*name\b", re.I),
        _AUTOCOMPLETE_MAP["name"],
    ),
    (re.compile(r"\be-?mail(?:\s+address)?\b", re.I), _AUTOCOMPLETE_MAP["email"]),
    (
        re.compile(r"\b(phone|mobile|cell|telephone)(?:\s+number)?\b", re.I),
        _AUTOCOMPLETE_MAP["tel"],
    ),
    (
        re.compile(r"\blinkedin(?:\s+(profile|url))?\b", re.I),
        FieldIntent("contact.linkedin", "contact.linkedin", "identity", "url"),
    ),
    (
        re.compile(r"\b(portfolio|personal\s+website|website\s+url)\b", re.I),
        FieldIntent("contact.portfolio", "contact.portfolio", "identity", "url"),
    ),
    (
        re.compile(r"\b(current\s+)?(employer|company)\s*(name)?\b", re.I),
        _AUTOCOMPLETE_MAP["organization"],
    ),
    (
        re.compile(r"\b(current\s+)?(job\s+)?title\b", re.I),
        _AUTOCOMPLETE_MAP["organization-title"],
    ),
    (
        re.compile(r"\b(school|college|university|institution)\s*(name)?\b", re.I),
        FieldIntent("education.institution", "education.0.institution", "identity"),
    ),
    (
        re.compile(r"\b(degree|highest\s+degree)\b", re.I),
        FieldIntent("education.degree", "education.0.degree", "identity"),
    ),
    (
        re.compile(r"\b(major|field\s+of\s+study|concentration)\b", re.I),
        FieldIntent("education.field", "education.0.field", "identity"),
    ),
    (
        re.compile(r"\bgraduation\s+(date|year)\b", re.I),
        FieldIntent("education.graduation", "education.0.end_date", "identity"),
    ),
    (
        re.compile(r"\b(current\s+)?location\b", re.I),
        FieldIntent("contact.location", "contact.location", "identity"),
    ),
    (re.compile(r"\bcity\b", re.I), _AUTOCOMPLETE_MAP["address-level2"]),
    (
        re.compile(r"\b(state|province|region)\b", re.I),
        _AUTOCOMPLETE_MAP["address-level1"],
    ),
    (
        re.compile(r"\b(zip|postal)\s*(code)?\b", re.I),
        _AUTOCOMPLETE_MAP["postal-code"],
    ),
    (re.compile(r"\bcountry\b", re.I), _AUTOCOMPLETE_MAP["country-name"]),
)


def _clean(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())


def _normalized(value: Any) -> str:
    return _NON_WORD_RE.sub(" ", _clean(value).casefold()).strip()


def split_person_name(full_name: str) -> dict[str, str]:
    """Conservatively derive common name components from one confirmed full name.

    This is generic transformation logic, not candidate-specific hard-coding.  It
    intentionally avoids culturally speculative reordering: first token becomes
    given name, final non-suffix token becomes family name, and intervening tokens
    remain the middle/additional name.
    """
    tokens = [token for token in _clean(full_name).split(" ") if token]
    if not tokens:
        return {"full_name": "", "first_name": "", "middle_name": "", "last_name": "", "suffix": ""}

    suffix = ""
    if len(tokens) > 1 and tokens[-1].rstrip(".").casefold() in _SUFFIXES:
        suffix = tokens.pop()

    if len(tokens) == 1:
        first, middle, last = tokens[0], "", ""
    else:
        first = tokens[0]
        last = tokens[-1]
        middle = " ".join(tokens[1:-1])

    return {
        "full_name": _clean(full_name),
        "first_name": first,
        "middle_name": middle,
        "last_name": last,
        "suffix": suffix,
    }


def parse_location(value: str) -> dict[str, str]:
    """Extract only structurally obvious location pieces; never guess missing data."""
    text = _clean(value)
    if not text:
        return {"full": "", "city": "", "region": "", "postal_code": "", "country": ""}
    parts = [part.strip() for part in text.split(",") if part.strip()]
    result = {"full": text, "city": "", "region": "", "postal_code": "", "country": ""}
    if parts:
        result["city"] = parts[0]
    if len(parts) >= 2:
        region_tokens = parts[1].split()
        if region_tokens:
            result["region"] = region_tokens[0]
        if len(region_tokens) >= 2 and re.fullmatch(r"[A-Za-z0-9 -]{3,12}", " ".join(region_tokens[1:])):
            result["postal_code"] = " ".join(region_tokens[1:])
    if len(parts) >= 3:
        result["country"] = parts[-1]
    return result


def field_intent(metadata: Mapping[str, Any] | None) -> FieldIntent | None:
    """Resolve a provider field into one semantic intent, or return None."""
    raw = dict(metadata or {})
    autocomplete = _clean(raw.get("autocomplete")).casefold()
    if autocomplete:
        # HTML autocomplete may contain section-* and billing/shipping prefixes.
        for token in reversed(autocomplete.split()):
            if token in _AUTOCOMPLETE_MAP:
                return _AUTOCOMPLETE_MAP[token]

    candidates = (
        raw.get("label"),
        raw.get("question"),
        raw.get("prompt"),
        raw.get("aria_label"),
        raw.get("placeholder"),
        raw.get("field_name"),
        raw.get("name"),
        raw.get("id"),
    )
    combined = " ".join(_clean(value) for value in candidates if _clean(value))
    if not combined:
        return None
    for pattern, intent in _LABEL_RULES:
        if pattern.search(combined):
            return intent
    return None


def requirement_from_field(
    metadata: Mapping[str, Any] | None,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    """Produce an Application Plan requirement for a recognized structural field."""
    intent = field_intent(metadata)
    if intent is None:
        return None
    raw = dict(metadata or {})
    label = _clean(
        raw.get("question")
        or raw.get("label")
        or raw.get("prompt")
        or raw.get("aria_label")
        or raw.get("placeholder")
        or raw.get("field_name")
        or raw.get("name")
        or intent.key
    )
    return {
        "question_key": intent.key,
        "question_family": "candidate_fact",
        "normalized_question": label or intent.key,
        "required": bool(required),
        "profile_fact_key": intent.profile_fact_key,
        "conditions": {"profile_projection": intent.projection},
        "sensitivity_class": "NORMAL",
        "autofill_default": True,
        "field_intelligence": {
            "version": INTELLIGENCE_VERSION,
            "confidence": intent.confidence,
            "projection": intent.projection,
        },
    }


def _snapshot_fact(snapshot: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    wanted = str(key or "").strip().casefold()
    for raw in snapshot.get("facts") or []:
        fact = dict(raw)
        if str(fact.get("key") or "").strip().casefold() == wanted:
            return fact
    return None


def _project(value: Any, projection: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        text = " | ".join(_clean(item) for item in value if _clean(item))
    else:
        text = _clean(value)
    if not text:
        return None

    projection = str(projection or "identity").strip().casefold()
    if projection == "identity":
        return text
    if projection in {"first_name", "middle_name", "last_name"}:
        result = split_person_name(text).get(projection, "")
        return result or None
    if projection.startswith("location_"):
        key = projection.removeprefix("location_")
        result = parse_location(text).get(key, "")
        return result or None
    return None


def resolve_profile_projection(
    *,
    profile_fact_key: str,
    projection: str,
) -> dict[str, Any]:
    """Resolve a structural field from the current confirmed Candidate Truth."""
    try:
        snapshot = truth_binding.current_candidate_profile_snapshot()
    except (LookupError, RuntimeError, ValueError) as error:
        return {
            "status": "NEEDS_INPUT",
            "reason": "candidate_truth_profile_unavailable",
            "detail": str(error),
        }

    fact = _snapshot_fact(snapshot, profile_fact_key)
    if fact is None:
        return {"status": "NEEDS_INPUT", "reason": "profile_fact_missing"}
    if fact.get("protected") is True:
        return {"status": "NEEDS_INPUT", "reason": "protected_profile_fact_requires_explicit_policy"}

    value = _project(fact.get("value"), projection)
    if not value:
        return {"status": "NEEDS_INPUT", "reason": "profile_projection_unavailable"}

    trust = str(fact.get("trust_level") or "UNKNOWN").upper()
    confidence = {
        "VERIFIED": 1.0,
        "USER_CONFIRMED": 1.0,
        "DOCUMENT_CONFIRMED": 0.98,
        "DERIVED": 0.8,
    }.get(trust, 0.0)
    if confidence <= 0:
        return {"status": "NEEDS_INPUT", "reason": "profile_fact_not_trusted"}

    return {
        "status": "ANSWERED",
        "resolution": "candidate_truth_projection",
        "answer": {
            "canonical_answer": value,
            "source": "candidate_truth_projection",
            "evidence_provenance": fact.get("source"),
            "confidence": confidence,
            # current_candidate_profile_snapshot() only returns the confirmed
            # profile snapshot; deterministic projection is therefore bound to
            # a candidate-confirmed profile revision, not an unreviewed draft.
            "user_confirmed": True,
            "autofill_allowed": True,
            "fact_key": fact.get("key"),
            "projection": projection,
            "candidate_truth_binding": truth_binding.public_binding_state(snapshot),
        },
    }
