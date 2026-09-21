"""Versioned Universal Candidate Ontology for MUNSHI Universal Brain.

This module is semantic metadata only.  It never reads candidate values and never
owns application execution.  Existing question-catalog, field-intelligence and
Apply semantic concepts are folded into one canonical vocabulary so new employer
wording can resolve to a stable concept without creating another answer system.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any, Iterable

from app import application_field_intelligence_v1 as fields
from app import application_question_catalog_v2 as catalog

ONTOLOGY_VERSION = "candidate-ontology-v1"

SENSITIVITY_RANK = {
    "NORMAL": 0,
    "PROTECTED": 1,
    "SELF_ID": 2,
    "SECRET": 3,
    "CREDENTIAL": 3,
    "POST_OFFER": 4,
}


@dataclass(frozen=True)
class CandidateConcept:
    concept_id: str
    category: str
    value_type: str
    sensitivity: str
    answer_mode: str
    autofill_default: bool
    aliases: tuple[str, ...]
    browser_semantics: tuple[str, ...]
    candidate_fact_paths: tuple[str, ...]
    legacy_paths: tuple[str, ...] = ()
    repeated_entity: bool = False
    description: str = ""


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = text.replace("u.s.", "us").replace("u.s", "us")
    text = text.replace("united states of america", "us").replace("united states", "us")
    text = text.replace("on-site", "onsite").replace("on site", "onsite")
    text = re.sub(r"[^a-z0-9+/#. ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _tokens(value: Any) -> set[str]:
    stop = {
        "a", "an", "and", "are", "can", "do", "for", "have", "is", "of", "or",
        "our", "please", "the", "this", "to", "what", "will", "you", "your",
    }
    return {part for part in _norm(value).split() if len(part) > 1 and part not in stop}


def _score(left: str, right: str) -> float:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = _tokens(a), _tokens(b)
    union = ta | tb
    jaccard = len(ta & tb) / len(union) if union else 0.0
    containment = max(
        len(ta & tb) / len(ta) if ta else 0.0,
        len(ta & tb) / len(tb) if tb else 0.0,
    )
    sequence = SequenceMatcher(None, a, b).ratio()
    return round((sequence * 0.42) + (jaccard * 0.33) + (containment * 0.25), 6)


def _answer_mode(key: str, family: str) -> str:
    if key in {"experience.total_years", "experience.relevant_years", "experience.skill_years", "experience.management", "experience.leadership"}:
        return "COMPUTED_FACT"
    if family == "work_authorization":
        return "WORK_AUTHORIZATION_POLICY"
    if family == "voluntary_self_identification":
        return "PROTECTED_POLICY"
    if family == "post_offer_sensitive":
        return "POST_OFFER_POLICY"
    if family == "open_ended_job_specific":
        return "OPEN_ENDED_JOB_SPECIFIC"
    if family in {"career_preference", "salary", "location", "availability"} and not key.startswith("contact."):
        return "PREFERENCE"
    return "DIRECT_FACT"


def _category(key: str, family: str) -> str:
    prefix = key.split(".", 1)[0].upper()
    return {
        "CONTACT": "CONTACT",
        "EDUCATION": "EDUCATION",
        "EMPLOYMENT": "EMPLOYMENT",
        "EXPERIENCE": "EXPERIENCE",
        "ELIGIBILITY": "WORK_AUTHORIZATION",
        "WORK": "WORK_PREFERENCE",
        "COMPENSATION": "COMPENSATION",
        "AVAILABILITY": "AVAILABILITY",
        "SCHEDULE": "SCHEDULE",
        "BACKGROUND": "BACKGROUND",
        "MOTIVATION": "OPEN_ENDED",
        "SELF_ID": "SELF_ID",
    }.get(prefix, family.upper())


def _catalog_concepts() -> dict[str, CandidateConcept]:
    result: dict[str, CandidateConcept] = {}
    for item in catalog.CONCEPTS:
        result[item.key] = CandidateConcept(
            concept_id=item.key,
            category=_category(item.key, item.family),
            value_type=item.answer_type,
            sensitivity=item.sensitivity_class,
            answer_mode=_answer_mode(item.key, item.family),
            autofill_default=bool(item.autofill_default),
            aliases=tuple(dict.fromkeys((item.key.replace(".", " "), *item.aliases))),
            browser_semantics=(),
            candidate_fact_paths=(item.profile_fact_key,) if item.profile_fact_key else (),
            repeated_entity=item.key.startswith(("education.", "employment.")),
            description=f"Canonical {item.family} concept {item.key}.",
        )
    return result


def _merge_structural_seed(registry: dict[str, CandidateConcept]) -> None:
    semantic_by_key: dict[str, list[str]] = {}
    fact_by_key: dict[str, str] = {}
    aliases_by_key: dict[str, list[str]] = {}
    for semantic, intent in fields._APPLY_SAFE_SEMANTIC_MAP.items():
        semantic_by_key.setdefault(intent.key, []).append(semantic)
        fact_by_key[intent.key] = intent.profile_fact_key
    for autocomplete, intent in fields._AUTOCOMPLETE_MAP.items():
        aliases_by_key.setdefault(intent.key, []).append(autocomplete.replace("-", " "))
        fact_by_key[intent.key] = intent.profile_fact_key

    structural_aliases: dict[str, tuple[str, ...]] = {
        "contact.first_name": ("first name", "given name", "forename"),
        "contact.middle_name": ("middle name", "additional name"),
        "contact.last_name": ("last name", "family name", "surname"),
        "contact.address_line1": ("street address", "address line 1", "mailing address", "home address"),
        "contact.address_line2": ("address line 2", "apartment", "suite", "unit"),
        "contact.city": ("city", "town", "municipality"),
        "contact.region": ("state", "state province", "province", "region", "residence state", "current state"),
        "contact.postal_code": ("zip", "zip code", "postal code"),
        "contact.country": ("country", "country of residence", "residence country"),
        "education.degree": ("degree", "degree type", "qualification"),
        "education.institution": ("school", "university", "college", "institution"),
        "education.field": ("major", "field of study", "concentration"),
        "education.graduation": ("graduation date", "graduation year"),
    }
    for key in sorted(set(semantic_by_key) | set(fact_by_key) | set(structural_aliases)):
        aliases = tuple(dict.fromkeys((key.replace(".", " "), *structural_aliases.get(key, ()), *aliases_by_key.get(key, ()))))
        semantics = tuple(sorted(set(semantic_by_key.get(key, ()))))
        fact = fact_by_key.get(key)
        current = registry.get(key)
        if current is None:
            registry[key] = CandidateConcept(
                concept_id=key,
                category=_category(key, "candidate_fact"),
                value_type="text",
                sensitivity="NORMAL",
                answer_mode="DIRECT_FACT",
                autofill_default=True,
                aliases=aliases,
                browser_semantics=semantics,
                candidate_fact_paths=(fact,) if fact else (key,),
                repeated_entity=key.startswith("education."),
                description=f"Structural candidate fact {key}.",
            )
        else:
            paths = tuple(dict.fromkeys((*current.candidate_fact_paths, *((fact,) if fact else ()))))
            registry[key] = replace(
                current,
                aliases=tuple(dict.fromkeys((*current.aliases, *aliases))),
                browser_semantics=tuple(dict.fromkeys((*current.browser_semantics, *semantics))),
                candidate_fact_paths=paths,
            )

    # Canonical migration compatibility: structural `education.degree` and the
    # historical catalog `education.highest_degree` are equivalent in meaning,
    # but remain separate IDs until all plan producers migrate.
    if "education.highest_degree" in registry and "education.degree" in registry:
        high = registry["education.highest_degree"]
        degree = registry["education.degree"]
        registry["education.highest_degree"] = replace(
            high,
            aliases=tuple(dict.fromkeys((*high.aliases, *degree.aliases))),
            browser_semantics=tuple(dict.fromkeys((*high.browser_semantics, *degree.browser_semantics))),
            candidate_fact_paths=tuple(dict.fromkeys((*high.candidate_fact_paths, *degree.candidate_fact_paths))),
            legacy_paths=("education.degree",),
        )
        registry["education.degree"] = replace(degree, legacy_paths=("education.highest_degree",))


def _build_registry() -> dict[str, CandidateConcept]:
    registry = _catalog_concepts()
    _merge_structural_seed(registry)
    # Additional stable preference/policy IDs used by Candidate Truth even when
    # no historic catalog question exists yet.
    extras = (
        CandidateConcept(
            "employment.employer", "EMPLOYMENT", "text", "NORMAL", "DIRECT_FACT", False,
            ("employer", "company name", "organization name"), ("EMPLOYER_NAME",),
            (), (), True, "Employer attached to one employment entity."
        ),
        CandidateConcept(
            "employment.title", "EMPLOYMENT", "text", "NORMAL", "DIRECT_FACT", False,
            ("job title", "position title", "role title"), ("JOB_TITLE",),
            (), (), True, "Title attached to one employment entity."
        ),
        CandidateConcept(
            "employment.location", "EMPLOYMENT", "text", "NORMAL", "DIRECT_FACT", False,
            ("work location", "employment location", "job location"), (),
            (), (), True, "Location attached to one employment entity."
        ),
        CandidateConcept(
            "employment.start_date", "EMPLOYMENT", "date", "NORMAL", "DIRECT_FACT", False,
            ("employment start date", "job start date", "start date at employer"), (),
            (), (), True, "Start date attached to one employment entity."
        ),
        CandidateConcept(
            "employment.end_date", "EMPLOYMENT", "date", "NORMAL", "DIRECT_FACT", False,
            ("employment end date", "job end date", "end date at employer"), (),
            (), (), True, "End date attached to one employment entity."
        ),
        CandidateConcept(
            "motivation.relevant_experience", "OPEN_ENDED_JOB_SPECIFIC", "long_text", "NORMAL",
            "OPEN_ENDED_JOB_SPECIFIC", False,
            (
                "describe your relevant experience", "tell us about your relevant experience",
                "describe experience relevant to this role", "summarize your relevant background",
                "how does your experience relate to this position",
            ),
            (), (), (), False,
            "Evidence-grounded summary of candidate experience relevant to the exact job snapshot.",
        ),
        CandidateConcept(
            "experience.capabilities", "EXPERIENCE", "list", "NORMAL", "ENUMERATION", False,
            (
                "skills and tools used", "tools and technologies used", "systems used",
                "software used", "technologies used", "platforms used",
                "which tools have you used", "what systems have you used",
            ),
            (), (), (), False,
            "Evidence-backed enumeration of explicit candidate skills, systems, tools or technologies.",
        ),
        CandidateConcept(
            "preference.work_mode", "WORK_PREFERENCE", "choice", "NORMAL", "PREFERENCE", True,
            ("preferred work mode", "work arrangement preference", "remote hybrid onsite preference"),
            (), ("application_defaults.work_modes",), (), False,
            "Explicit candidate preference for remote, hybrid, or onsite work.",
        ),
        CandidateConcept(
            "work.authorization.us", "WORK_AUTHORIZATION", "boolean", "PROTECTED", "WORK_AUTHORIZATION_POLICY", False,
            ("authorized to work in the us", "us work authorization"),
            ("WORK_AUTHORIZATION_CURRENT",), ("application_defaults.authorized_to_work",),
            ("eligibility.work_authorized_us",), False,
            "Protected US work-authorization policy concept.",
        ),
        CandidateConcept(
            "work.sponsorship.future", "WORK_AUTHORIZATION", "boolean", "PROTECTED", "WORK_AUTHORIZATION_POLICY", False,
            ("future sponsorship", "need sponsorship in the future", "require sponsorship now or in the future"),
            ("SPONSORSHIP_FUTURE",), ("application_defaults.sponsorship_required",),
            ("eligibility.sponsorship_future",), False,
            "Protected future sponsorship policy concept.",
        ),
    )
    for item in extras:
        if item.concept_id not in registry:
            registry[item.concept_id] = item

    capability_aliases = {
        "recruiting": ("recruiting", "recruitment", "recruitment operations"),
        "technical_recruiting": ("technical recruiting", "tech recruiting"),
        "candidate_sourcing": ("candidate sourcing", "talent sourcing", "sourcing candidates"),
        "resume_screening": ("resume screening", "cv screening", "screening resumes"),
        "interviewing": ("interviewing", "conducted interviews", "candidate interviews"),
        "onboarding": ("onboarding", "new hire onboarding", "employee onboarding"),
        "i9": ("i-9", "i9", "form i-9"),
        "documentation": ("documentation", "employee documentation", "hr documentation"),
        "hr_operations": ("hr operations", "human resources operations"),
        "employee_records": ("employee records", "personnel records"),
        "compliance": ("compliance", "hr compliance"),
        "audit_support": ("audit support", "audits", "audit preparation"),
        "training_coordination": ("training coordination", "training scheduling"),
        "payroll_support": ("payroll support", "payroll administration", "payroll"),
        "timekeeping": ("timekeeping", "time keeping", "timesheets"),
        "analytics": ("analytics", "data analytics", "people analytics", "hr analytics"),
        "dashboarding": ("dashboarding", "dashboards", "dashboard development"),
        "data_cleaning": ("data cleaning", "data cleansing"),
        "excel": ("excel", "microsoft excel"),
        "power_bi": ("power bi", "powerbi"),
        "workday": ("workday",),
        "greenhouse": ("greenhouse", "greenhouse ats"),
        "bamboohr": ("bamboohr", "bamboo hr"),
        "successfactors": ("successfactors", "sap successfactors"),
        "adp": ("adp", "adp workforce now"),
        "ukg": ("ukg", "ukg pro", "ultimate kronos group"),
        "python": ("python",),
        "tableau": ("tableau",),
    }
    for slug, aliases in capability_aliases.items():
        key = f"skill.{slug}"
        registry.setdefault(
            key,
            CandidateConcept(
                key, "SKILL", "boolean", "NORMAL", "EXPERIENCE_EVIDENCE_BOOLEAN", False,
                tuple(dict.fromkeys((key.replace(".", " "), *aliases))), (), (), (), False,
                f"Evidence-backed capability concept for {slug.replace('_', ' ')}.",
            ),
        )
    return registry


_REGISTRY = _build_registry()


def validate_registry() -> None:
    if len(_REGISTRY) < len(catalog.CONCEPTS):
        raise RuntimeError("Universal ontology lost current catalog concepts.")
    for key, concept in _REGISTRY.items():
        if key != concept.concept_id or not key or key != key.casefold():
            raise RuntimeError(f"Invalid canonical concept id: {key!r}")
        if concept.sensitivity not in SENSITIVITY_RANK:
            raise RuntimeError(f"Unsupported sensitivity for {key}.")
        if not concept.aliases:
            raise RuntimeError(f"Concept {key} has no semantic aliases.")
        if concept.sensitivity != "NORMAL" and concept.autofill_default:
            raise RuntimeError(f"Protected concept {key} cannot default to autofill.")


validate_registry()


def all_concepts() -> tuple[CandidateConcept, ...]:
    return tuple(_REGISTRY[key] for key in sorted(_REGISTRY))


def _dynamic_concept(concept_id: str) -> CandidateConcept | None:
    key = str(concept_id or "").strip().casefold()
    if not key:
        return None
    repeated = re.fullmatch(
        r"(?P<group>experience|education|projects|skills|certifications)\.(?P<index>\d+)\.(?P<field>[a-z0-9_]+)",
        key,
    )
    if repeated:
        group = repeated.group("group")
        field = repeated.group("field")
        value_type = "list" if field in {"bullets", "details", "tools", "skills"} else "text"
        return CandidateConcept(
            concept_id=key,
            category=group.upper(),
            value_type=value_type,
            sensitivity="NORMAL",
            answer_mode="DIRECT_FACT",
            autofill_default=False,
            aliases=(key.replace(".", " "),),
            browser_semantics=(),
            candidate_fact_paths=(key,),
            repeated_entity=True,
            description=f"Entity-scoped profile fact {key}.",
        )
    if key.startswith("experience.skill.") and len(key) <= 180:
        label = key.removeprefix("experience.skill.").replace("_", " ").replace(".", " ")
        return CandidateConcept(
            concept_id=key, category="EXPERIENCE", value_type="boolean", sensitivity="NORMAL",
            answer_mode="EXPERIENCE_EVIDENCE_BOOLEAN", autofill_default=False,
            aliases=(label, f"experience with {label}", f"used {label}"), browser_semantics=(),
            candidate_fact_paths=(), repeated_entity=False,
            description=f"Evidence-backed capability concept for {label}.",
        )
    if key in {"profile.professional_summary", "languages"} or key.startswith("application_defaults."):
        if key in {
            "application_defaults.gender", "application_defaults.race",
            "application_defaults.race_ethnicity", "application_defaults.ethnicity",
            "application_defaults.veteran_status", "application_defaults.disability_status",
        }:
            sensitivity = "SELF_ID"
        elif key in {
            "application_defaults.work_authorization_country",
            "application_defaults.authorization_basis",
            "application_defaults.visa_or_permit",
            "application_defaults.sponsorship_required",
            "application_defaults.authorized_to_work",
        }:
            sensitivity = "PROTECTED"
        else:
            sensitivity = "NORMAL"
        mode = "WORK_AUTHORIZATION_POLICY" if sensitivity == "PROTECTED" else "DIRECT_FACT"
        return CandidateConcept(
            concept_id=key, category="PROFILE", value_type="text", sensitivity=sensitivity,
            answer_mode=mode, autofill_default=False, aliases=(key.replace(".", " "),),
            browser_semantics=(), candidate_fact_paths=(key,), repeated_entity=False,
            description=f"Profile-scoped canonical fact {key}.",
        )
    return None


def concept(concept_id: str) -> CandidateConcept | None:
    key = str(concept_id or "").strip().casefold()
    return _REGISTRY.get(key) or _dynamic_concept(key)


def require_concept(concept_id: str) -> CandidateConcept:
    item = concept(concept_id)
    if item is None:
        raise LookupError(f"Unknown Universal Brain concept: {concept_id}")
    return item


def concepts_for_browser_semantic(semantic_type: Any) -> tuple[CandidateConcept, ...]:
    wanted = str(semantic_type or "").strip().upper()
    if not wanted:
        return ()
    return tuple(item for item in all_concepts() if wanted in item.browser_semantics)


def concepts_for_profile_fact(fact_key: Any) -> tuple[CandidateConcept, ...]:
    wanted = str(fact_key or "").strip().casefold()
    if not wanted:
        return ()
    return tuple(
        item for item in all_concepts()
        if wanted in {str(path).casefold() for path in (*item.candidate_fact_paths, *item.legacy_paths)}
    )


def retrieve(
    text: str,
    *,
    browser_semantic: str = "",
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Value-free lexical/semantic retrieval over canonical concept metadata."""
    raw = str(text or "").strip()
    semantic = str(browser_semantic or "").strip().upper()
    scored: list[dict[str, Any]] = []
    for item in all_concepts():
        alias_scores = [_score(raw, alias) for alias in item.aliases] if raw else [0.0]
        score = max(alias_scores or [0.0])
        if semantic and semantic in item.browser_semantics:
            score = max(score, 0.995)
        scored.append(
            {
                "concept_id": item.concept_id,
                "score": round(score, 6),
                "sensitivity": item.sensitivity,
                "answer_mode": item.answer_mode,
                "category": item.category,
            }
        )
    scored.sort(key=lambda row: (-float(row["score"]), str(row["concept_id"])))
    return scored[: max(1, int(top_k))]


def legacy_question_family(concept_id: str) -> str:
    """Map a canonical concept into the existing Answer/Application Plan family.

    This is a compatibility adapter only; concept identity remains the ontology ID.
    """
    item = require_concept(concept_id)
    key = item.concept_id
    if item.answer_mode == "OPEN_ENDED_JOB_SPECIFIC":
        return "open_ended_job_specific"
    if item.answer_mode in {"EXPERIENCE_EVIDENCE_BOOLEAN", "SKILL_EVIDENCE", "COMPUTED_FACT", "ENUMERATION"}:
        return "experience_skill"
    if item.category == "COMPENSATION" or key.startswith("compensation."):
        return "salary"
    if item.category == "AVAILABILITY" or key.startswith(("availability.", "schedule.")):
        return "availability"
    if key.startswith((
        "contact.address", "contact.city", "contact.region",
        "contact.postal", "contact.country", "contact.location",
    )):
        return "location"
    if item.category in {"WORK_PREFERENCE", "LOCATION"} or key.startswith(("work.relocation", "preference.", "location.")):
        return "career_preference"
    if item.category == "WORK_AUTHORIZATION" or item.sensitivity != "NORMAL":
        return "work_authorization"
    return "candidate_fact"


def sensitivity_at_least(current: str, candidate: str) -> str:
    """Return the stricter sensitivity.  A browser hint can never downgrade it."""
    left = str(current or "NORMAL").upper()
    right = str(candidate or "NORMAL").upper()
    if left not in SENSITIVITY_RANK or right not in SENSITIVITY_RANK:
        raise ValueError("Unsupported sensitivity class.")
    return left if SENSITIVITY_RANK[left] >= SENSITIVITY_RANK[right] else right


def public_inventory() -> list[dict[str, Any]]:
    return [
        {
            "concept_id": item.concept_id,
            "category": item.category,
            "value_type": item.value_type,
            "sensitivity": item.sensitivity,
            "answer_mode": item.answer_mode,
            "autofill_default": item.autofill_default,
            "aliases": list(item.aliases),
            "browser_semantics": list(item.browser_semantics),
            "candidate_fact_paths": list(item.candidate_fact_paths),
            "legacy_paths": list(item.legacy_paths),
            "repeated_entity": item.repeated_entity,
        }
        for item in all_concepts()
    ]
