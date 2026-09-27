"""Multi-lane, value-free employer question understanding for Universal Brain."""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from app import answer_brain
from app import application_field_intelligence_v1 as field_intelligence
from app import application_question_catalog_v2 as catalog
from app import candidate_ontology_v1 as ontology
from app import universal_semantic_embedding_v1 as semantic_embedding

RESOLVER_VERSION = "universal-question-resolver-v1"


@dataclass(frozen=True)
class QuestionObservation:
    raw_question: str = ""
    label: str = ""
    section_heading: str = ""
    nearby_text: str = ""
    placeholder: str = ""
    name: str = ""
    id: str = ""
    autocomplete: str = ""
    input_type: str = ""
    role: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    apply_semantic_type: str = "UNKNOWN"
    apply_semantic_confidence: float = 0.0
    control_id: str = ""
    page_id: str = ""
    repeat_group: str | None = None
    repeat_index: int | None = None
    previous_labels: tuple[str, ...] = field(default_factory=tuple)
    next_labels: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "QuestionObservation":
        value = dict(raw or {})
        def text(*keys: str) -> str:
            for key in keys:
                candidate = value.get(key)
                if candidate is not None and str(candidate).strip():
                    return " ".join(str(candidate).split())[:4000]
            return ""
        options_raw = value.get("options") or value.get("optionLabels") or ()
        if isinstance(options_raw, (str, bytes)):
            options_raw = (str(options_raw),)
        options = tuple(" ".join(str(item).split())[:500] for item in options_raw if str(item).strip())[:250]
        prev = value.get("previous_labels") or value.get("previousLabels") or ()
        nxt = value.get("next_labels") or value.get("nextLabels") or ()
        def string_tuple(items: Any) -> tuple[str, ...]:
            if isinstance(items, (str, bytes)):
                items = (items,)
            return tuple(" ".join(str(item).split())[:500] for item in (items or ()) if str(item).strip())[:20]
        repeat_index = value.get("repeat_index", value.get("repeatIndex"))
        try:
            repeat_index = int(repeat_index) if repeat_index is not None else None
        except (TypeError, ValueError):
            repeat_index = None
        try:
            semantic_conf = float(value.get("apply_semantic_confidence", value.get("semanticConfidence", 0.0)) or 0.0)
        except (TypeError, ValueError):
            semantic_conf = 0.0
        return cls(
            raw_question=text("raw_question", "rawText", "question", "prompt", "normalized_question"),
            label=text("label"), section_heading=text("section_heading", "sectionHeading", "section"),
            nearby_text=text("nearby_text", "nearbyText", "contextText"), placeholder=text("placeholder"),
            name=text("name", "fieldName", "field_name"), id=text("id"), autocomplete=text("autocomplete"),
            input_type=text("input_type", "inputType", "type"), role=text("role"), options=options,
            apply_semantic_type=text("apply_semantic_type", "semanticType") or "UNKNOWN",
            apply_semantic_confidence=max(0.0, min(1.0, semantic_conf)),
            control_id=text("control_id", "controlId", "provider_field_id"), page_id=text("page_id", "pageId"),
            repeat_group=text("repeat_group", "repeatGroupId") or None, repeat_index=repeat_index,
            previous_labels=string_tuple(prev), next_labels=string_tuple(nxt),
        )

    def as_field_metadata(self) -> dict[str, Any]:
        return {
            "semanticType": self.apply_semantic_type,
            "semanticConfidence": self.apply_semantic_confidence,
            "rawText": self.raw_question,
            "question": self.raw_question,
            "label": self.label,
            "sectionHeading": self.section_heading,
            "nearbyText": self.nearby_text,
            "placeholder": self.placeholder,
            "name": self.name,
            "id": self.id,
            "autocomplete": self.autocomplete,
            "inputType": self.input_type,
            "role": self.role,
            "options": list(self.options),
            "controlId": self.control_id,
            "pageId": self.page_id,
            "repeatGroupId": self.repeat_group,
            "repeatIndex": self.repeat_index,
        }


def model_semantics_enabled() -> bool:
    return str(os.getenv("MUNSHI_UNIVERSAL_MODEL_SEMANTICS_ENABLED") or "").strip().casefold() in {"1", "true", "yes", "on"}


def _embedding_thresholds() -> tuple[float, float]:
    try:
        minimum = float(os.getenv("MUNSHI_UNIVERSAL_EMBEDDING_MIN_SCORE") or "0.84")
    except ValueError:
        minimum = 0.84
    try:
        margin = float(os.getenv("MUNSHI_UNIVERSAL_EMBEDDING_MARGIN") or "0.05")
    except ValueError:
        margin = 0.05
    return max(0.0, min(1.0, minimum)), max(0.0, min(0.5, margin))


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold().replace("on-site", "onsite")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/#. ]+", " ", text)).strip()



_US_CA_SUBDIVISIONS = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
        "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
        "maine", "maryland", "massachusetts", "michigan", "minnesota",
        "mississippi", "missouri", "montana", "nebraska", "nevada",
        "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming", "district of columbia",
        "washington dc", "puerto rico", "guam", "american samoa",
        "northern mariana islands", "us virgin islands",
        "alberta", "british columbia", "manitoba", "new brunswick",
        "newfoundland and labrador", "northwest territories", "nova scotia",
        "nunavut", "ontario", "prince edward island", "quebec",
        "saskatchewan", "yukon",
        "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
        "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
        "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
        "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
        "vt", "va", "wa", "wv", "wi", "wy", "dc", "ab", "bc", "mb", "nb",
        "nl", "nt", "ns", "nu", "on", "pe", "qc", "sk", "yt",
    }
)

_OPTION_PLACEHOLDERS = frozenset(
    {
        "", "select", "select one", "choose", "choose one", "please select",
        "please choose", "not applicable", "n a", "na", "other",
    }
)


def _looks_like_subdivision_options(options: Sequence[str]) -> bool:
    usable = [
        _norm(item)
        for item in options
        if _norm(item) not in _OPTION_PLACEHOLDERS
    ][:120]
    if len(usable) < 3:
        return False
    hits = sum(item in _US_CA_SUBDIVISIONS for item in usable)
    return hits >= 3 and hits / len(usable) >= 0.60


def _context_flags(obs: QuestionObservation) -> dict[str, bool]:
    section = _norm(obs.section_heading)
    visible = _norm(_visible_text(obs))
    repeat = _norm(obs.repeat_group or "")
    employment = (
        repeat in {"employment", "experience", "work history", "work experience"}
        or any(
            token in section
            for token in ("employment", "work experience", "work history", "job history")
        )
        or bool(re.search(r"\b(employer|job title|work history|employment)\b", visible))
    )
    education = (
        repeat in {"education", "education history", "schools"}
        or any(token in section for token in ("education", "school", "academic"))
    )
    contact = (
        not employment
        and not education
        and any(
            token in section
            for token in (
                "address", "contact", "location", "personal information",
                "personal details", "candidate information", "basic information",
            )
        )
    )
    return {"employment": employment, "education": education, "contact": contact}


def _candidate_row(concept_id: str, score: float) -> dict[str, Any]:
    item = ontology.require_concept(concept_id)
    return {
        "concept_id": item.concept_id,
        "score": round(float(score), 6),
        "sensitivity": item.sensitivity,
        "answer_mode": item.answer_mode,
        "category": item.category,
        "description": item.description,
    }


def _model_candidates(
    obs: QuestionObservation,
    ranked: Sequence[Mapping[str, Any]],
    *,
    sensitivity_floor: str,
) -> list[dict[str, Any]]:
    """Prepare a closed semantic candidate set using browser structure only."""
    rows: dict[str, dict[str, Any]] = {}
    for raw in ranked:
        concept_id = str(raw.get("concept_id") or "").strip().casefold()
        item = ontology.concept(concept_id)
        if item is None:
            continue
        if sensitivity_floor == "NORMAL" and item.sensitivity != "NORMAL":
            continue
        if sensitivity_floor != "NORMAL" and item.sensitivity != sensitivity_floor:
            continue
        row = dict(raw)
        rows[item.concept_id] = row

    flags = _context_flags(obs)
    if sensitivity_floor == "NORMAL" and flags["contact"]:
        # Personal/contact fields should be offered contact concepts even when
        # embedding retrieval uses unusual wording. Employment/education
        # concepts are structurally out of scope unless their own section/repeat
        # context says otherwise.
        rows = {
            key: row
            for key, row in rows.items()
            if str(row.get("category") or "").upper()
            not in {"EMPLOYMENT", "EDUCATION", "WORK_AUTHORIZATION", "SELF_ID"}
        }
        for concept_id, score in (
            ("contact.region", 0.82),
            ("contact.city", 0.80),
            ("contact.country", 0.78),
            ("contact.postal_code", 0.76),
            ("contact.address_line1", 0.74),
            ("contact.address_line2", 0.72),
        ):
            if ontology.concept(concept_id) is not None:
                current = rows.get(concept_id)
                if current is None or float(current.get("score") or 0.0) < score:
                    rows[concept_id] = _candidate_row(concept_id, score)

    ordered = sorted(
        rows.values(),
        key=lambda row: (-float(row.get("score") or 0.0), str(row.get("concept_id") or "")),
    )
    return ordered[:8]


def _visible_text(obs: QuestionObservation) -> str:
    return " ".join(
        part for part in (
            obs.label, obs.raw_question, obs.placeholder, obs.name, obs.section_heading,
            obs.nearby_text, *obs.previous_labels, *obs.next_labels,
        ) if part
    )[:12000]


def _primary_texts(obs: QuestionObservation) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        value for value in (
            obs.label, obs.raw_question, obs.placeholder, obs.name,
            f"{obs.section_heading} {obs.label}".strip(), _visible_text(obs),
        ) if value
    ))


def _preclassification(obs: QuestionObservation) -> dict[str, Any]:
    visible = obs.raw_question or obs.label or obs.placeholder or obs.name
    family = "unknown"
    if visible:
        try:
            family = answer_brain.classify_question(visible)
        except ValueError:
            family = "unknown"
    catalog_match = catalog.match_question(visible) if visible else {"matched": False}
    sensitivity = "NORMAL"
    concept_id = None
    if catalog_match.get("matched"):
        sensitivity = str(catalog_match.get("sensitivity_class") or "NORMAL").upper()
        concept_id = str(catalog_match.get("canonical_concept") or catalog_match.get("question_key") or "") or None
    if family == "work_authorization":
        sensitivity = ontology.sensitivity_at_least(sensitivity, "PROTECTED")
    elif family == "voluntary_self_identification":
        sensitivity = ontology.sensitivity_at_least(sensitivity, "SELF_ID")
    elif family == "credential_requirement":
        sensitivity = ontology.sensitivity_at_least(sensitivity, "CREDENTIAL")
    elif family == "post_offer_sensitive":
        sensitivity = ontology.sensitivity_at_least(sensitivity, "POST_OFFER")
    semantic = str(obs.apply_semantic_type or "").upper()
    if semantic in {
        "WORK_AUTHORIZATION_CURRENT", "WORK_LOCATION_ELIGIBILITY", "AGE_18_PLUS",
        "SPONSORSHIP_CURRENT", "SPONSORSHIP_FUTURE",
        "IMMIGRATION_ASSISTANCE", "SECURITY_CLEARANCE",
    }:
        sensitivity = ontology.sensitivity_at_least(sensitivity, "PROTECTED")
    if semantic in {
        "VETERAN_STATUS", "PROTECTED_VETERAN_STATUS", "DISABILITY_STATUS",
        "GENDER", "RACE_ETHNICITY", "HISPANIC_LATINO", "EEO_SELF_ID",
    }:
        sensitivity = ontology.sensitivity_at_least(sensitivity, "SELF_ID")
    if semantic == "EMPLOYEE_ID":
        sensitivity = ontology.sensitivity_at_least(sensitivity, "POST_OFFER")
    if semantic in {"PASSWORD", "OTP", "MFA", "CAPTCHA", "PASSKEY", "BIOMETRIC", "IDENTITY_VERIFICATION"}:
        sensitivity = ontology.sensitivity_at_least(sensitivity, "CREDENTIAL")
    return {"family": family, "sensitivity": sensitivity, "catalog_match": catalog_match, "catalog_concept_id": concept_id}


def _result(
    *,
    state: str,
    concept_id: str | None,
    confidence: float,
    lane: str,
    sensitivity: str,
    reason_code: str,
    conditions: Mapping[str, Any] | None = None,
    candidates: Sequence[Mapping[str, Any]] = (),
    preclassification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "resolver_version": RESOLVER_VERSION,
        "state": state,
        "concept_id": concept_id,
        "confidence": round(float(confidence), 6),
        "classification_lane": lane,
        "sensitivity": sensitivity,
        "reason_code": reason_code,
        "conditions": dict(conditions or {}),
        "candidates": [dict(item) for item in candidates[:8]],
        "preclassification": dict(preclassification or {}),
        "candidate_values_exposed": False,
    }


def _repeat_scoped_concept(obs: QuestionObservation, concept_id: str) -> str:
    if obs.repeat_group is None or obs.repeat_index is None:
        return concept_id
    group = _norm(obs.repeat_group).replace(" ", "_")
    index = int(obs.repeat_index)
    semantic = str(obs.apply_semantic_type or "").strip().upper()
    if group in {"employment", "experience", "work_history", "work_experience"}:
        semantic_fields = {
            "EMPLOYER_NAME": "employer", "JOB_TITLE": "title",
            "EMPLOYMENT_START_DATE": "start_date", "EMPLOYMENT_END_DATE": "end_date",
        }
        canonical_fields = {
            "employment.current_employer": "employer",
            "employment.current_title": "title",
        }
        field = semantic_fields.get(semantic) or canonical_fields.get(concept_id)
        if field:
            return f"experience.{index}.{field}"
    if group in {"education", "education_history", "schools"}:
        semantic_fields = {
            "SCHOOL_NAME": "institution", "DEGREE": "degree",
            "FIELD_OF_STUDY": "field", "GRADUATION_DATE": "end_date",
        }
        canonical_fields = {
            "education.institution": "institution", "education.degree": "degree",
            "education.highest_degree": "degree", "education.field": "field",
            "education.graduation": "end_date",
        }
        field = semantic_fields.get(semantic) or canonical_fields.get(concept_id)
        if field:
            return f"education.{index}.{field}"
    return concept_id


def _resolved_concept(obs: QuestionObservation, concept_id: str) -> ontology.CandidateConcept:
    return ontology.require_concept(_repeat_scoped_concept(obs, concept_id))


_DYNAMIC_ENUMERATION_PATTERNS = (
    re.compile(
        r"^(?:which|what)\s+(?P<category>.*?)\s*(?:tools|systems|software|technologies|platforms|skills|applications|programs)\s+(?:have|do|are|can|did)\s+you\b.*$",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:list|select|identify|name|indicate)\s+(?:all\s+)?(?:the\s+)?(?P<category>.*?)\s*(?:tools|systems|software|technologies|platforms|skills|applications|programs)\b.*$",
        re.I,
    ),
)


def _dynamic_enumeration_concept(obs: QuestionObservation) -> tuple[str, float, str, dict[str, Any]] | None:
    raw = " ".join((obs.label or obs.raw_question or "").split()).strip()
    if not raw or len(raw) > 800:
        return None
    blocked = {
        "work authorization", "sponsorship", "visa", "citizenship", "salary",
        "compensation", "race", "ethnicity", "gender", "disability", "veteran",
        "password", "otp", "captcha", "social security", "ssn",
    }
    for pattern in _DYNAMIC_ENUMERATION_PATTERNS:
        match = pattern.fullmatch(raw)
        if not match:
            continue
        category = " ".join(str(match.group("category") or "").strip(" .,:;/-").split())
        lowered = category.casefold()
        if any(term in lowered for term in blocked):
            return None
        return (
            "experience.capabilities",
            0.96,
            "DYNAMIC_CAPABILITY_ENUMERATION",
            {"filter_text": category},
        )
    return None


_DYNAMIC_CAPABILITY_PATTERNS = (
    re.compile(r"^(?:do you have|have you had|do you possess)\s+(?:any\s+)?experience\s+(?:with|using|in)\s+(?P<skill>.+?)[?.!]*$", re.I),
    re.compile(r"^(?:have you|did you)\s+(?:ever\s+)?(?:used|worked with|utilized|utilised)\s+(?P<skill>.+?)[?.!]*$", re.I),
    re.compile(r"^(?:are you|would you consider yourself)\s+(?:proficient|experienced|skilled)\s+(?:with|in)\s+(?P<skill>.+?)[?.!]*$", re.I),
    re.compile(r"^(?:experience|knowledge|proficiency)\s+(?:with|of|in)\s+(?P<skill>.+?)[?.!]*$", re.I),
)


def _dynamic_capability_concept(obs: QuestionObservation) -> tuple[str, float, str] | None:
    raw = " ".join((obs.label or obs.raw_question or "").split()).strip()
    if not raw or len(raw) > 500 or re.search(r"\byears?\b", raw, re.I):
        return None
    for pattern in _DYNAMIC_CAPABILITY_PATTERNS:
        match = pattern.fullmatch(raw)
        if not match:
            continue
        skill = " ".join(match.group("skill").strip(" .?!").split())
        normalized = re.sub(r"[^a-z0-9]+", "_", skill.casefold()).strip("_")[:100]
        if not normalized or len(skill) > 120:
            return None
        blocked = {
            "work authorization", "sponsorship", "visa", "citizenship", "salary",
            "compensation", "race", "ethnicity", "gender", "disability", "veteran",
            "password", "otp", "captcha", "social security", "ssn",
        }
        lowered = skill.casefold()
        if any(term in lowered for term in blocked):
            return None
        concept_id = f"experience.skill.{normalized}"
        if ontology.concept(concept_id) is None:
            return None
        return concept_id, 0.96, "DYNAMIC_CAPABILITY_GRAMMAR"
    return None


def _context_candidate(obs: QuestionObservation) -> tuple[str, float, str] | None:
    text = _norm(" ".join((obs.label, obs.raw_question, obs.placeholder, obs.name)))
    section = _norm(obs.section_heading)
    control_is_choice = (
        _norm(obs.role) in {"combobox", "listbox", "radio", "radiogroup"}
        or _norm(obs.input_type) in {"select", "select-one", "radio"}
        or bool(obs.options)
    )
    flags = _context_flags(obs)
    address_context = flags["contact"] or any(
        token in section for token in ("address", "contact", "location")
    )

    if re.search(r"\b(state|province|region)\b", text):
        score = 0.98 if address_context or control_is_choice else 0.92
        return "contact.region", score, "ADDRESS_REGION_CONTEXT"
    if re.search(r"\b(zip|postal)(?:\s+code)?\b", text):
        return "contact.postal_code", 0.98 if address_context else 0.94, "POSTAL_CONTEXT"
    if re.search(r"\bcity\b", text) and (address_context or control_is_choice):
        return "contact.city", 0.97, "ADDRESS_CITY_CONTEXT"
    if re.search(r"\bcountry\b", text) and (address_context or control_is_choice):
        return "contact.country", 0.97, "ADDRESS_COUNTRY_CONTEXT"
    if re.search(r"\b(street\s+address|address\s*(line)?\s*1|mailing\s+address|home\s+address)\b", text):
        return "contact.address_line1", 0.98, "ADDRESS_LINE1_CONTEXT"
    if re.search(r"\b(address\s*(line)?\s*2|apt|apartment|suite|unit)\b", text):
        return "contact.address_line2", 0.98, "ADDRESS_LINE2_CONTEXT"

    # Provider-neutral option-shape recognition. If a non-employment personal
    # field offers a set dominated by known state/province labels, the control is
    # a region selector regardless of employer wording such as "domicile
    # jurisdiction" or "home jurisdiction". No candidate-selected value is used.
    region_options = control_is_choice and _looks_like_subdivision_options(obs.options)
    residence_cue = bool(
        re.search(
            r"\b(home|residen(?:ce|tial|cy)?|domicil(?:e|iary)?|jurisdiction|address)\b",
            text,
        )
    )
    if (
        region_options
        and not flags["employment"]
        and not flags["education"]
        and (address_context or residence_cue)
    ):
        return "contact.region", 0.97, "SUBDIVISION_OPTION_CONTEXT"

    return None


def resolve_question(
    observation: QuestionObservation | Mapping[str, Any],
    *,
    model_interpreter: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)
    pre = _preclassification(obs)
    sensitivity_floor = str(pre["sensitivity"])

    # A full-question exact catalog meaning outranks token-level structural
    # heuristics (e.g. "why ... company" must not become employer-name).
    # Sensitivity still uses the strict preclassification floor, so this rule can
    # never downgrade a protected browser observation.
    catalog_match = dict(pre.get("catalog_match") or {})
    catalog_concept_id = str(pre.get("catalog_concept_id") or "").strip().casefold()
    if (
        catalog_match.get("matched") is True
        and float(catalog_match.get("confidence") or 0.0) >= 0.995
        and catalog_concept_id
        and ontology.concept(catalog_concept_id) is not None
    ):
        item = _resolved_concept(obs, catalog_concept_id)
        sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
        return _result(
            state="RESOLVED", concept_id=item.concept_id,
            confidence=float(catalog_match.get("confidence") or 1.0),
            lane="DETERMINISTIC_CATALOG_EXACT", sensitivity=sensitivity,
            reason_code="FULL_QUESTION_EXACT_SEMANTICS",
            conditions=dict(catalog_match.get("conditions") or {}),
            preclassification=pre,
        )

    # Lane A: existing deterministic structural semantics remain next.
    intent = field_intelligence.field_intent(obs.as_field_metadata())
    if intent is not None and ontology.concept(intent.key) is not None:
        item = _resolved_concept(obs, intent.key)
        sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
        return _result(
            state="RESOLVED", concept_id=item.concept_id, confidence=float(intent.confidence),
            lane="DETERMINISTIC_STRUCTURAL", sensitivity=sensitivity,
            reason_code="FIELD_INTELLIGENCE", preclassification=pre,
        )

    cat = dict(pre.get("catalog_match") or {})
    if cat.get("matched"):
        key = str(cat.get("canonical_concept") or cat.get("question_key") or "")
        if ontology.concept(key) is not None:
            item = _resolved_concept(obs, key)
            sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
            return _result(
                state="RESOLVED", concept_id=item.concept_id,
                confidence=float(cat.get("confidence") or 1.0), lane="DETERMINISTIC_CATALOG",
                sensitivity=sensitivity, reason_code=str(cat.get("match_type") or "CATALOG"),
                conditions=dict(cat.get("conditions") or {}), preclassification=pre,
            )

    semantic_candidates = ontology.concepts_for_browser_semantic(obs.apply_semantic_type)
    if len(semantic_candidates) == 1:
        item = _resolved_concept(obs, semantic_candidates[0].concept_id)
        sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
        return _result(
            state="RESOLVED", concept_id=item.concept_id,
            confidence=max(0.93, obs.apply_semantic_confidence), lane="DETERMINISTIC_BROWSER_SEMANTIC",
            sensitivity=sensitivity, reason_code="UNIQUE_BROWSER_SEMANTIC", preclassification=pre,
        )

    dynamic_enumeration = _dynamic_enumeration_concept(obs)
    if dynamic_enumeration is not None and sensitivity_floor == "NORMAL":
        key, score, reason, conditions = dynamic_enumeration
        item = _resolved_concept(obs, key)
        return _result(
            state="RESOLVED", concept_id=item.concept_id, confidence=score,
            lane="ONTOLOGY_DYNAMIC_ENUMERATION", sensitivity="NORMAL",
            reason_code=reason, conditions=conditions, preclassification=pre,
        )

    dynamic_capability = _dynamic_capability_concept(obs)
    if dynamic_capability is not None and sensitivity_floor == "NORMAL":
        key, score, reason = dynamic_capability
        item = _resolved_concept(obs, key)
        return _result(
            state="RESOLVED", concept_id=item.concept_id, confidence=score,
            lane="ONTOLOGY_DYNAMIC_CAPABILITY", sensitivity="NORMAL",
            reason_code=reason, preclassification=pre,
        )

    # Lane B: provider-neutral structural context. Run this before embedding so
    # obvious controls (address/region/city/postal/country) stay deterministic
    # and fast instead of paying local-model latency.
    contextual = _context_candidate(obs)
    if contextual is not None:
        key, score, reason = contextual
        item = _resolved_concept(obs, key)
        sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
        return _result(
            state="RESOLVED", concept_id=item.concept_id, confidence=score,
            lane="ONTOLOGY_CONTEXT", sensitivity=sensitivity, reason_code=reason,
            preclassification=pre,
        )

    # Lane C/D: retrieve over aliases/descriptions independently for each visible
    # fragment, then retain the best score per concept. Candidate values are never
    # part of this retrieval.
    best: dict[str, dict[str, Any]] = {}
    for text in _primary_texts(obs):
        for row in ontology.retrieve(text, browser_semantic=obs.apply_semantic_type, top_k=10):
            current = best.get(str(row["concept_id"]))
            if current is None or float(row["score"]) > float(current["score"]):
                best[str(row["concept_id"])] = row
    ranked = sorted(best.values(), key=lambda row: (-float(row["score"]), str(row["concept_id"])))
    if ranked:
        top = ranked[0]
        second = float(ranked[1]["score"]) if len(ranked) > 1 else 0.0
        if float(top["score"]) >= 0.88 and (float(top["score"]) == 1.0 or float(top["score"]) - second >= 0.06):
            item = _resolved_concept(obs, str(top["concept_id"]))
            sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
            return _result(
                state="RESOLVED", concept_id=item.concept_id, confidence=float(top["score"]),
                lane="ONTOLOGY_RETRIEVAL", sensitivity=sensitivity,
                reason_code="ALIAS_OR_SEMANTIC_RETRIEVAL", candidates=ranked, preclassification=pre,
            )

    # Lane C: standalone local semantic embeddings. This is intentionally
    # candidate-value-free and provider-local (Ollama). A fuzzy embedding match
    # may resolve directly only for NORMAL fields and only above both a score and
    # separation-margin gate. Otherwise it merely improves the candidate set used
    # by context/model interpretation.
    embedded = semantic_embedding.retrieve_fail_closed(obs.as_field_metadata(), top_k=8)
    if embedded:
        minimum, margin = _embedding_thresholds()
        top_embed = embedded[0]
        second_embed = float(embedded[1]["score"]) if len(embedded) > 1 else 0.0
        if (
            sensitivity_floor == "NORMAL"
            and float(top_embed["score"]) >= minimum
            and float(top_embed["score"]) - second_embed >= margin
            and ontology.concept(str(top_embed["concept_id"])) is not None
        ):
            item = _resolved_concept(obs, str(top_embed["concept_id"]))
            if item.sensitivity == "NORMAL":
                return _result(
                    state="RESOLVED", concept_id=item.concept_id,
                    confidence=float(top_embed["score"]), lane="ONTOLOGY_EMBEDDING",
                    sensitivity="NORMAL", reason_code="LOCAL_SEMANTIC_EMBEDDING",
                    candidates=embedded, preclassification=pre,
                )
        merged: dict[str, dict[str, Any]] = {
            str(row["concept_id"]): dict(row) for row in ranked
        }
        for row in embedded:
            key = str(row["concept_id"])
            current = merged.get(key)
            if current is None or float(row["score"]) > float(current["score"]):
                merged[key] = dict(row)
        ranked = sorted(
            merged.values(),
            key=lambda row: (-float(row["score"]), str(row["concept_id"])),
        )[:10]

    # Lane E: bounded model semantic interpretation. The callback receives only
    # browser structure and ontology candidates; candidate values/secrets are absent.
    if model_semantics_enabled() and model_interpreter is not None and sensitivity_floor not in {"CREDENTIAL", "SECRET", "POST_OFFER"}:
        payload = {
            "resolver_version": RESOLVER_VERSION,
            "observation": {
                "raw_question": obs.raw_question,
                "label": obs.label,
                "section_heading": obs.section_heading,
                "nearby_text": obs.nearby_text,
                "placeholder": obs.placeholder,
                "name": obs.name,
                "input_type": obs.input_type,
                "role": obs.role,
                "options": list(obs.options)[:100],
                "apply_semantic_type": obs.apply_semantic_type,
                "repeat_group": obs.repeat_group,
                "repeat_index": obs.repeat_index,
            },
            "ontology_candidates": _model_candidates(
                obs, ranked, sensitivity_floor=sensitivity_floor
            ),
            "candidate_values": None,
        }
        response = dict(model_interpreter(payload) or {})
        key = str(response.get("concept_id") or "").strip().casefold()
        try:
            confidence = float(response.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        item = ontology.concept(key)
        if item is not None and confidence >= 0.92:
            sensitivity = ontology.sensitivity_at_least(item.sensitivity, sensitivity_floor)
            return _result(
                state="RESOLVED", concept_id=item.concept_id, confidence=confidence,
                lane="BOUNDED_MODEL_INTERPRETATION", sensitivity=sensitivity,
                reason_code=str(response.get("reason_code") or "MODEL_SEMANTIC_INTERPRETATION")[:120],
                candidates=ranked, preclassification=pre,
            )

    if ranked and float(ranked[0]["score"]) >= 0.55:
        return _result(
            state="AMBIGUOUS_CONCEPT", concept_id=None, confidence=float(ranked[0]["score"]),
            lane="UNRESOLVED", sensitivity=sensitivity_floor, reason_code="AMBIGUOUS_ONTOLOGY_CANDIDATES",
            candidates=ranked, preclassification=pre,
        )
    return _result(
        state="UNKNOWN", concept_id=None, confidence=0.0, lane="UNRESOLVED",
        sensitivity=sensitivity_floor, reason_code="NO_SAFE_SEMANTIC_MATCH",
        candidates=ranked, preclassification=pre,
    )


def public_observation(observation: QuestionObservation | Mapping[str, Any]) -> dict[str, Any]:
    obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)
    return asdict(obs)
