"""Value-free multi-lane semantic understanding for MUNSHI Universal Brain."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Mapping, Sequence

from app import answer_brain
from app import application_field_intelligence_v1 as field_intelligence
from app import application_question_catalog_v2 as question_catalog
from app import candidate_ontology_v1 as ontology

RESOLVER_VERSION = "universal-question-resolver-v1"
MODEL_SEMANTICS_ENV = "MUNSHI_UNIVERSAL_MODEL_SEMANTICS_ENABLED"

_PROTECTED_SEMANTICS = {
    "WORK_AUTHORIZATION_CURRENT": "eligibility.work_authorized_us",
    "SPONSORSHIP_CURRENT": "eligibility.sponsorship_now",
    "SPONSORSHIP_FUTURE": "eligibility.sponsorship_future",
    "IMMIGRATION_ASSISTANCE": "eligibility.sponsorship_future",
    "SECURITY_CLEARANCE": "background.clearance",
    "VETERAN_STATUS": "self_id.veteran",
    "PROTECTED_VETERAN_STATUS": "self_id.veteran",
    "DISABILITY_STATUS": "self_id.disability",
    "GENDER": "self_id.gender",
    "RACE_ETHNICITY": "self_id.race_ethnicity",
    "EEO_SELF_ID": "self_id.race_ethnicity",
    "BACKGROUND_CHECK": "background.background_check",
    "DRUG_SCREENING": "background.drug_screen",
}

_SECURITY_PATTERNS = (
    ("CAPTCHA", re.compile(r"\bcaptcha\b|i am not a robot|recaptcha|hcaptcha", re.I)),
    ("MFA", re.compile(r"\b(mfa|multi[- ]?factor|two[- ]?factor|2fa|one[- ]?time (?:code|password)|otp)\b", re.I)),
    ("PASSKEY", re.compile(r"\bpasskey\b|security key|webauthn", re.I)),
    ("BIOMETRIC", re.compile(r"\bbiometric|fingerprint|face id|touch id\b", re.I)),
    ("PASSWORD", re.compile(r"\bpassword|passcode|security answer\b", re.I)),
)

_US_STATE_NAMES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut","delaware",
    "florida","georgia","hawaii","idaho","illinois","indiana","iowa","kansas","kentucky",
    "louisiana","maine","maryland","massachusetts","michigan","minnesota","mississippi","missouri",
    "montana","nebraska","nevada","new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island","south carolina",
    "south dakota","tennessee","texas","utah","vermont","virginia","washington","west virginia",
    "wisconsin","wyoming","district of columbia",
}


def _text(value: Any, maximum: int = 4000) -> str:
    return " ".join(str(value or "").split())[:maximum]


def _list_text(values: Any, *, limit: int = 200) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    result: list[str] = []
    for raw in values[:limit]:
        if isinstance(raw, Mapping):
            value = raw.get("label") or raw.get("text") or raw.get("value") or ""
        else:
            value = raw
        item = _text(value, 240)
        if item:
            result.append(item)
    return tuple(result)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold())).strip()


@dataclass(frozen=True)
class QuestionObservation:
    raw_question: str = ""
    label: str = ""
    section_heading: str = ""
    nearby_text: str = ""
    placeholder: str = ""
    name: str = ""
    html_id: str = ""
    autocomplete: str = ""
    input_type: str = ""
    role: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    apply_semantic_type: str = "UNKNOWN"
    apply_semantic_confidence: float = 0.0
    control_id: str = ""
    page_id: str = ""
    repeat_group: str = ""
    repeat_index: int | None = None
    neighboring_labels: tuple[str, ...] = field(default_factory=tuple)
    required: bool = True

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "QuestionObservation":
        data = dict(raw or {})
        repeat_index = data.get("repeatIndex", data.get("repeat_index"))
        try:
            repeat_index = int(repeat_index) if repeat_index is not None else None
        except (TypeError, ValueError):
            repeat_index = None
        semantic_confidence = data.get("semanticConfidence", data.get("apply_semantic_confidence", 0.0))
        try:
            semantic_confidence = float(semantic_confidence or 0.0)
        except (TypeError, ValueError):
            semantic_confidence = 0.0
        return cls(
            raw_question=_text(data.get("raw_question") or data.get("rawText") or data.get("question") or data.get("prompt")),
            label=_text(data.get("label")),
            section_heading=_text(data.get("section_heading") or data.get("sectionHeading") or data.get("section")),
            nearby_text=_text(data.get("nearby_text") or data.get("nearbyText")),
            placeholder=_text(data.get("placeholder")),
            name=_text(data.get("name") or data.get("fieldName") or data.get("field_name")),
            html_id=_text(data.get("id") or data.get("html_id")),
            autocomplete=_text(data.get("autocomplete")),
            input_type=_text(data.get("input_type") or data.get("inputType") or data.get("type")),
            role=_text(data.get("role")),
            options=_list_text(data.get("options")),
            apply_semantic_type=_text(data.get("apply_semantic_type") or data.get("semanticType") or "UNKNOWN", 120).upper() or "UNKNOWN",
            apply_semantic_confidence=max(0.0, min(1.0, semantic_confidence)),
            control_id=_text(data.get("control_id") or data.get("controlId"), 500),
            page_id=_text(data.get("page_id") or data.get("pageId"), 500),
            repeat_group=_text(data.get("repeat_group") or data.get("repeatGroup") or data.get("repeatGroupId"), 500),
            repeat_index=repeat_index,
            neighboring_labels=_list_text(data.get("neighboring_labels") or data.get("neighboringLabels"), limit=20),
            required=bool(data.get("required", True)),
        )

    def semantic_text(self) -> str:
        parts = (
            self.label,
            self.raw_question,
            self.placeholder,
            self.name,
            self.section_heading,
            self.nearby_text,
            *self.neighboring_labels,
        )
        return " | ".join(part for part in parts if part)

    def value_free_payload(self, *, ontology_candidates: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
        """Payload permitted for bounded semantic interpretation; no candidate store values."""
        return {
            "resolver_version": RESOLVER_VERSION,
            "question": {
                "raw_question": self.raw_question,
                "label": self.label,
                "section_heading": self.section_heading,
                "nearby_text": self.nearby_text,
                "placeholder": self.placeholder,
                "name": self.name,
                "id": self.html_id,
                "autocomplete": self.autocomplete,
                "input_type": self.input_type,
                "role": self.role,
                "options": list(self.options[:100]),
                "apply_semantic_type": self.apply_semantic_type,
                "apply_semantic_confidence": self.apply_semantic_confidence,
                "repeat_group": self.repeat_group or None,
                "repeat_index": self.repeat_index,
                "neighboring_labels": list(self.neighboring_labels),
                "required": self.required,
            },
            "ontology_candidates": [dict(item) for item in ontology_candidates[:8]],
            "candidate_values_included": False,
        }


def model_semantics_enabled() -> bool:
    return str(os.getenv(MODEL_SEMANTICS_ENV) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _security_checkpoint(observation: QuestionObservation) -> dict[str, Any] | None:
    text = observation.semantic_text()
    for checkpoint, pattern in _SECURITY_PATTERNS:
        if pattern.search(text):
            return {
                "status": "SECURITY_CHECKPOINT",
                "checkpoint": checkpoint,
                "classification_lane": "SECURITY_PRECLASSIFICATION",
                "confidence": 1.0,
                "resolver_version": RESOLVER_VERSION,
            }
    return None


def _field_metadata(observation: QuestionObservation) -> dict[str, Any]:
    return {
        "semanticType": observation.apply_semantic_type,
        "semanticConfidence": observation.apply_semantic_confidence,
        "rawText": observation.raw_question,
        "label": observation.label,
        "sectionHeading": observation.section_heading,
        "nearbyText": observation.nearby_text,
        "placeholder": observation.placeholder,
        "name": observation.name,
        "id": observation.html_id,
        "autocomplete": observation.autocomplete,
        "type": observation.input_type,
        "role": observation.role,
        "options": list(observation.options),
        "controlId": observation.control_id,
        "repeatGroupId": observation.repeat_group or None,
        "repeatIndex": observation.repeat_index,
        "required": observation.required,
    }


def _repeat_adjust(concept_id: str, observation: QuestionObservation) -> str:
    if not observation.repeat_group and observation.repeat_index is None:
        return concept_id
    group = _norm(observation.repeat_group + " " + observation.section_heading)
    if "experience" in group or "employment" in group or "work history" in group:
        return {
            "employment.current_employer": "employment.employer",
            "employment.current_title": "employment.title",
        }.get(concept_id, concept_id)
    if "education" in group or "school" in group:
        return {
            "education.highest_degree": "education.degree",
        }.get(concept_id, concept_id)
    return concept_id


def _lane_a(observation: QuestionObservation) -> dict[str, Any] | None:
    semantic = observation.apply_semantic_type
    if semantic in _PROTECTED_SEMANTICS:
        concept_id = _PROTECTED_SEMANTICS[semantic]
        concept = ontology.concept(concept_id)
        if concept:
            return {
                "concept_id": concept.concept_id,
                "confidence": max(0.99, observation.apply_semantic_confidence),
                "classification_lane": "DETERMINISTIC_PROTECTED_SEMANTIC",
                "reason_code": semantic,
            }

    requirement = field_intelligence.requirement_from_field(_field_metadata(observation), required=observation.required)
    if requirement is not None:
        concept_id = _repeat_adjust(str(requirement["question_key"]), observation)
        if ontology.concept(concept_id):
            return {
                "concept_id": concept_id,
                "confidence": float((requirement.get("field_intelligence") or {}).get("confidence") or 1.0),
                "classification_lane": "DETERMINISTIC_STRUCTURAL",
                "reason_code": str((requirement.get("field_intelligence") or {}).get("classification_source") or "field_intelligence"),
            }

    visible = observation.label or observation.raw_question or observation.placeholder
    if visible:
        matched = question_catalog.match_question(visible)
        if matched.get("matched"):
            concept_id = _repeat_adjust(str(matched["canonical_concept"]), observation)
            if ontology.concept(concept_id):
                return {
                    "concept_id": concept_id,
                    "confidence": float(matched.get("confidence") or 0.0),
                    "classification_lane": "DETERMINISTIC_QUESTION_CATALOG",
                    "reason_code": str(matched.get("match_type") or "catalog"),
                }
    return None


def _looks_like_state_selector(observation: QuestionObservation) -> bool:
    labels = _norm(" ".join((observation.label, observation.raw_question, observation.placeholder, observation.name)))
    section = _norm(observation.section_heading + " " + " ".join(observation.neighboring_labels))
    state_word = bool(re.search(r"\b(state|province|region)\b", labels))
    address_context = any(token in section for token in ("address", "contact", "location", "city", "postal", "zip", "country"))
    option_names = {_norm(item) for item in observation.options}
    state_option_count = sum(name in _US_STATE_NAMES for name in option_names)
    selector = observation.input_type.casefold() in {"select", "choice"} or observation.role.casefold() in {"combobox", "listbox"}
    return state_word and (address_context or (selector and state_option_count >= 5))


def _looks_like_country_selector(observation: QuestionObservation) -> bool:
    labels = _norm(" ".join((observation.label, observation.raw_question, observation.placeholder, observation.name)))
    return "country" in labels and (
        "address" in _norm(observation.section_heading) or len(observation.options) >= 5
    )


def _context_override(observation: QuestionObservation) -> dict[str, Any] | None:
    if _looks_like_state_selector(observation):
        return {
            "concept_id": "contact.region",
            "confidence": 0.995,
            "classification_lane": "ONTOLOGY_CONTEXT",
            "reason_code": "STATE_SELECTION_CONTEXT",
        }
    if _looks_like_country_selector(observation):
        return {
            "concept_id": "contact.country",
            "confidence": 0.99,
            "classification_lane": "ONTOLOGY_CONTEXT",
            "reason_code": "COUNTRY_SELECTION_CONTEXT",
        }
    return None


def _ontology_candidates(observation: QuestionObservation) -> list[dict[str, Any]]:
    return ontology.retrieve(
        observation.semantic_text(),
        browser_semantic=observation.apply_semantic_type,
        top_k=8,
    )


def resolve_question(
    observation: QuestionObservation | Mapping[str, Any],
    *,
    model_interpreter: Callable[[dict[str, Any]], Mapping[str, Any]] | None = None,
    minimum_score: float = 0.78,
    minimum_margin: float = 0.07,
) -> dict[str, Any]:
    obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)
    security = _security_checkpoint(obs)
    if security:
        return security

    deterministic = _lane_a(obs)
    if deterministic:
        item = ontology.require_concept(str(deterministic["concept_id"]))
        return {
            "status": "RESOLVED",
            **deterministic,
            "sensitivity": item.sensitivity,
            "answer_mode": item.answer_mode,
            "repeated_entity": item.repeated_entity,
            "resolver_version": RESOLVER_VERSION,
        }

    context = _context_override(obs)
    if context:
        item = ontology.require_concept(str(context["concept_id"]))
        return {
            "status": "RESOLVED",
            **context,
            "sensitivity": item.sensitivity,
            "answer_mode": item.answer_mode,
            "repeated_entity": item.repeated_entity,
            "resolver_version": RESOLVER_VERSION,
        }

    candidates = _ontology_candidates(obs)
    if candidates:
        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else {"score": 0.0}
        top_score = float(top["score"])
        margin = top_score - float(runner_up["score"])
        if top_score >= minimum_score and (top_score >= 0.995 or margin >= minimum_margin):
            concept_id = _repeat_adjust(str(top["concept_id"]), obs)
            item = ontology.require_concept(concept_id)
            return {
                "status": "RESOLVED",
                "concept_id": concept_id,
                "confidence": top_score,
                "classification_lane": "ONTOLOGY_RETRIEVAL",
                "reason_code": "ALIAS_OR_SEMANTIC_RETRIEVAL",
                "sensitivity": item.sensitivity,
                "answer_mode": item.answer_mode,
                "repeated_entity": item.repeated_entity,
                "resolver_version": RESOLVER_VERSION,
            }

    if model_interpreter is not None and model_semantics_enabled():
        payload = obs.value_free_payload(ontology_candidates=candidates)
        interpreted = dict(model_interpreter(payload) or {})
        concept_id = str(interpreted.get("concept_id") or "").strip().casefold()
        try:
            confidence = float(interpreted.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        allowed_ids = {str(item["concept_id"]) for item in candidates}
        if concept_id in allowed_ids and confidence >= 0.90:
            item = ontology.require_concept(concept_id)
            return {
                "status": "RESOLVED",
                "concept_id": concept_id,
                "confidence": min(1.0, confidence),
                "classification_lane": "BOUNDED_MODEL_SEMANTICS",
                "reason_code": _text(interpreted.get("reason_code") or "MODEL_INTERPRETATION", 120),
                "sensitivity": item.sensitivity,
                "answer_mode": item.answer_mode,
                "repeated_entity": item.repeated_entity,
                "resolver_version": RESOLVER_VERSION,
                "candidate_values_exposed_to_model": False,
            }

    top = candidates[0] if candidates else None
    runner = candidates[1] if len(candidates) > 1 else None
    if top and float(top["score"]) >= 0.50:
        return {
            "status": "AMBIGUOUS_CONCEPT",
            "classification_lane": "ONTOLOGY_RETRIEVAL",
            "top_candidate": {"concept_id": top["concept_id"], "score": top["score"], "sensitivity": top["sensitivity"]},
            "runner_up": ({"concept_id": runner["concept_id"], "score": runner["score"], "sensitivity": runner["sensitivity"]} if runner else None),
            "resolver_version": RESOLVER_VERSION,
        }
    return {
        "status": "UNKNOWN_CONCEPT",
        "classification_lane": "EXHAUSTED",
        "resolver_version": RESOLVER_VERSION,
    }


def observation_fingerprint(observation: QuestionObservation | Mapping[str, Any]) -> str:
    obs = observation if isinstance(observation, QuestionObservation) else QuestionObservation.from_mapping(observation)
    payload = {
        "question": _norm(obs.raw_question or obs.label),
        "section": _norm(obs.section_heading),
        "input_type": _norm(obs.input_type),
        "role": _norm(obs.role),
        "option_signature": hashlib.sha256(json.dumps(sorted(_norm(item) for item in obs.options), separators=(",", ":")).encode()).hexdigest() if obs.options else "",
        "repeat_group": _norm(obs.repeat_group),
        "repeat_index": obs.repeat_index,
        "page_id": _norm(obs.page_id),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
