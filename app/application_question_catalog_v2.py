"""Canonical application-question catalog and conservative similarity resolver.

Fifty semantic concepts intentionally expose exactly two common application-form
phrasings each: 100 bounded common questions.  Provider wording is normalized
into a stable key before MUNSHI Apply or AutoApply asks Answer Brain / Application
Plan for authority.

Matching is deterministic and fail-closed.  Low-confidence or ambiguous wording
stays unresolved instead of guessing a candidate answer.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

CATALOG_VERSION = "application-question-catalog-v2"


@dataclass(frozen=True)
class QuestionConcept:
    key: str
    family: str
    answer_type: str
    sensitivity_class: str
    profile_fact_key: str | None
    autofill_default: bool
    aliases: tuple[str, str]


def _concept(
    key: str,
    family: str,
    answer_type: str,
    sensitivity_class: str,
    profile_fact_key: str | None,
    autofill_default: bool,
    first: str,
    second: str,
) -> QuestionConcept:
    return QuestionConcept(
        key=key,
        family=family,
        answer_type=answer_type,
        sensitivity_class=sensitivity_class,
        profile_fact_key=profile_fact_key,
        autofill_default=autofill_default,
        aliases=(first, second),
    )


CONCEPTS: tuple[QuestionConcept, ...] = (
    _concept(
        "contact.full_name", "candidate_fact", "text", "NORMAL",
        "contact.full_name", True,
        "What is your full legal name?", "Please enter your full name.",
    ),
    _concept(
        "contact.email", "candidate_fact", "email", "NORMAL",
        "contact.email", True,
        "What is your email address?", "Please provide your email.",
    ),
    _concept(
        "contact.phone", "candidate_fact", "phone", "NORMAL",
        "contact.phone", True,
        "What is your phone number?", "Please provide your mobile number.",
    ),
    _concept(
        "contact.location", "location", "text", "NORMAL",
        "contact.location", True,
        "Where are you currently located?", "What is your current location?",
    ),
    _concept(
        "contact.linkedin", "candidate_fact", "url", "NORMAL",
        "contact.linkedin", True,
        "What is your LinkedIn profile URL?", "Please provide your LinkedIn profile.",
    ),
    _concept(
        "contact.portfolio", "candidate_fact", "url", "NORMAL",
        "contact.portfolio", True,
        "Do you have a portfolio or personal website?", "Please provide your portfolio URL.",
    ),
    _concept(
        "employment.current_employer", "candidate_fact", "text", "NORMAL",
        "experience.0.employer", True,
        "Who is your current employer?", "What company do you currently work for?",
    ),
    _concept(
        "employment.current_title", "candidate_fact", "text", "NORMAL",
        "experience.0.title", True,
        "What is your current job title?", "Please enter your current position title.",
    ),
    _concept(
        "eligibility.work_authorized_us", "work_authorization", "boolean", "PROTECTED",
        "application_defaults.authorized_to_work", False,
        "Are you legally authorized to work in the United States?",
        "Are you currently authorized to work in the US?",
    ),
    _concept(
        "eligibility.sponsorship_now", "work_authorization", "boolean", "PROTECTED",
        "application_defaults.sponsorship_required", False,
        "Will you now require employment visa sponsorship?",
        "Do you currently require sponsorship to work?",
    ),
    _concept(
        "eligibility.sponsorship_future", "work_authorization", "boolean", "PROTECTED",
        "application_defaults.sponsorship_required", False,
        "Will you require sponsorship now or in the future?",
        "Will you need visa sponsorship in the future?",
    ),
    _concept(
        "eligibility.visa_status", "work_authorization", "text", "PROTECTED",
        "application_defaults.visa_or_permit", False,
        "What is your current visa or work permit status?",
        "Please specify your work authorization status.",
    ),
    _concept(
        "eligibility.age_18", "candidate_fact", "boolean", "NORMAL", None, False,
        "Are you at least 18 years of age?", "Are you 18 years old or older?",
    ),
    _concept(
        "availability.start_date", "availability", "date", "NORMAL", None, False,
        "When are you available to start?", "What is your earliest available start date?",
    ),
    _concept(
        "availability.start_immediately", "availability", "boolean", "NORMAL",
        "application_defaults.start_immediately", True,
        "Are you available to start immediately?", "Can you begin work right away?",
    ),
    _concept(
        "availability.notice_period", "availability", "text", "NORMAL", None, False,
        "What is your current notice period?", "How much notice do you need before starting?",
    ),
    _concept(
        "work.relocation", "location", "boolean", "NORMAL",
        "application_defaults.willing_to_relocate", True,
        "Are you willing to relocate for this position?", "Would you relocate for this role?",
    ),
    _concept(
        "work.remote", "career_preference", "boolean", "NORMAL",
        "application_defaults.work_modes", False,
        "Are you comfortable working remotely?", "Are you open to a remote work arrangement?",
    ),
    _concept(
        "work.hybrid", "career_preference", "boolean", "NORMAL",
        "application_defaults.work_modes", False,
        "Are you comfortable with a hybrid work schedule?", "Are you open to hybrid work?",
    ),
    _concept(
        "work.onsite", "career_preference", "boolean", "NORMAL",
        "application_defaults.in_person_ok", True,
        "Are you willing to work onsite?", "Can you work in person at this location?",
    ),
    _concept(
        "work.travel", "career_preference", "text", "NORMAL", None, False,
        "Are you willing to travel for work?", "What percentage of travel are you comfortable with?",
    ),
    _concept(
        "work.transport", "career_preference", "boolean", "NORMAL",
        "application_defaults.has_transport", True,
        "Do you have reliable transportation?", "Do you have dependable transportation to work?",
    ),
    _concept(
        "work.commute", "location", "boolean", "NORMAL", None, False,
        "Are you able to commute to this work location?", "Can you reliably commute to the office?",
    ),
    _concept(
        "compensation.salary", "salary", "text", "NORMAL", None, False,
        "What are your salary expectations?", "What is your desired annual compensation?",
    ),
    _concept(
        "compensation.hourly", "salary", "text", "NORMAL", None, False,
        "What hourly rate are you seeking?", "What is your desired hourly pay rate?",
    ),
    _concept(
        "experience.total_years", "experience_skill", "number", "NORMAL", None, False,
        "How many total years of professional experience do you have?",
        "How many years of work experience do you have?",
    ),
    _concept(
        "experience.relevant_years", "experience_skill", "number", "NORMAL", None, False,
        "How many years of relevant experience do you have?",
        "How many years of experience do you have in a similar role?",
    ),
    _concept(
        "experience.skill_years", "experience_skill", "number", "NORMAL", None, False,
        "How many years of experience do you have with this skill?",
        "How many years have you used this technology professionally?",
    ),
    _concept(
        "experience.management", "experience_skill", "number", "NORMAL", None, False,
        "How many years of management experience do you have?",
        "How many years have you managed people or teams?",
    ),
    _concept(
        "experience.leadership", "experience_skill", "number", "NORMAL", None, False,
        "How many years of leadership experience do you have?",
        "How many years have you worked in a leadership role?",
    ),
    _concept(
        "education.highest_degree", "candidate_fact", "text", "NORMAL",
        "education.0.degree", True,
        "What is the highest level of education you have completed?", "What is your highest degree?",
    ),
    _concept(
        "education.field", "candidate_fact", "text", "NORMAL",
        "education.0.field", True,
        "What was your field of study?", "What is your degree concentration or major?",
    ),
    _concept(
        "education.institution", "candidate_fact", "text", "NORMAL",
        "education.0.institution", True,
        "Which school or university did you attend?", "What is the name of your university?",
    ),
    _concept(
        "education.graduation", "candidate_fact", "text", "NORMAL",
        "education.0.end_date", True,
        "When did you graduate?", "What is your graduation date?",
    ),
    _concept(
        "education.gpa", "candidate_fact", "text", "NORMAL",
        "education.0.gpa", False,
        "What is your GPA?", "Please provide your grade point average.",
    ),
    _concept(
        "background.prior_employee", "candidate_fact", "boolean", "NORMAL",
        "application_defaults.prior_employee", True,
        "Have you previously worked for this company?", "Are you a former employee of our organization?",
    ),
    _concept(
        "background.noncompete", "candidate_fact", "boolean", "NORMAL", None, False,
        "Are you subject to a non-compete agreement?",
        "Do you have any restrictive covenant that may affect this role?",
    ),
    _concept(
        "background.background_check", "post_offer_sensitive", "boolean", "POST_OFFER", None, False,
        "Are you willing to undergo a background check?",
        "Can you consent to a pre-employment background screening?",
    ),
    _concept(
        "background.drug_screen", "post_offer_sensitive", "boolean", "POST_OFFER", None, False,
        "Are you willing to complete a drug screening?", "Can you pass a pre-employment drug test?",
    ),
    _concept(
        "background.clearance", "candidate_fact", "text", "NORMAL",
        "application_defaults.government_clearance", False,
        "Do you currently hold a security clearance?",
        "What level of government security clearance do you hold?",
    ),
    _concept(
        "schedule.shift", "career_preference", "text", "NORMAL", None, False,
        "What shifts are you available to work?", "Are you able to work the required shift?",
    ),
    _concept(
        "schedule.weekend", "career_preference", "boolean", "NORMAL", None, False,
        "Are you available to work weekends?", "Can you work Saturdays or Sundays if required?",
    ),
    _concept(
        "schedule.overtime", "career_preference", "boolean", "NORMAL", None, False,
        "Are you willing to work overtime when needed?", "Can you work additional hours if required?",
    ),
    _concept(
        "motivation.why_company", "open_ended_job_specific", "long_text", "NORMAL", None, False,
        "Why do you want to work for our company?", "Why are you interested in joining this organization?",
    ),
    _concept(
        "motivation.why_role", "open_ended_job_specific", "long_text", "NORMAL", None, False,
        "Why are you interested in this position?", "Why do you want this role?",
    ),
    _concept(
        "motivation.reason_leaving", "open_ended_job_specific", "long_text", "NORMAL", None, False,
        "Why are you looking to leave your current role?",
        "What is your reason for seeking a new opportunity?",
    ),
    _concept(
        "self_id.veteran", "voluntary_self_identification", "choice", "SELF_ID", None, False,
        "What is your veteran status?", "Do you identify as a protected veteran?",
    ),
    _concept(
        "self_id.disability", "voluntary_self_identification", "choice", "SELF_ID", None, False,
        "Do you have a disability or have you had one in the past?",
        "Please select your disability status.",
    ),
    _concept(
        "self_id.gender", "voluntary_self_identification", "choice", "SELF_ID", None, False,
        "What is your gender?", "How do you self-identify your gender?",
    ),
    _concept(
        "self_id.race_ethnicity", "voluntary_self_identification", "choice", "SELF_ID", None, False,
        "What is your race or ethnicity?", "Please select your race or ethnic background.",
    ),
)

assert len(CONCEPTS) == 50
assert sum(len(item.aliases) for item in CONCEPTS) == 100

_NORMALIZE_RE = re.compile(r"[^a-z0-9+/#. ]+")
_SPACE_RE = re.compile(r"\s+")
_SKILL_YEARS_PATTERNS = (
    re.compile(
        r"how many years(?: of)? experience do you have (?:with|using|in) "
        r"(?P<skill>.+?)(?:\?|$)",
        re.I,
    ),
    re.compile(
        r"how many years have you (?:used|worked with) (?P<skill>.+?)"
        r"(?: professionally)?(?:\?|$)",
        re.I,
    ),
)


def normalize_question(value: str) -> str:
    text = str(value or "").strip().casefold()
    replacements = (
        ("united states of america", "us"),
        ("united states", "us"),
        ("u.s.a.", "us"),
        ("u.s.a", "us"),
        ("u.s.", "us"),
        ("u.s", "us"),
        ("on-site", "onsite"),
        ("on site", "onsite"),
        ("work from home", "remote"),
        ("authorised", "authorized"),
        ("e-mail", "email"),
        ("mobile phone", "phone"),
        ("telephone", "phone"),
        ("compensation expectation", "salary expectation"),
    )
    for source, target in replacements:
        text = text.replace(source, target)
    text = _NORMALIZE_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip(" .")


def _tokens(value: str) -> set[str]:
    stop = {
        "a", "an", "and", "are", "can", "do", "for", "have", "is", "of",
        "or", "our", "please", "the", "this", "to", "what", "will", "you",
        "your",
    }
    return {
        part
        for part in normalize_question(value).split()
        if part not in stop and len(part) > 1
    }


def _score(question: str, alias: str) -> float:
    left = normalize_question(question)
    right = normalize_question(alias)
    if left == right:
        return 1.0
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left, right).ratio()
    return round((sequence * 0.58) + (jaccard * 0.42), 6)


def _slug(value: str) -> str:
    normalized = re.sub(
        r"[^a-z0-9]+", ".", normalize_question(value)
    ).strip(".")
    return normalized[:80] or "unknown"


def _dynamic_skill_match(question: str) -> dict[str, Any] | None:
    raw = str(question or "").strip()
    for pattern in _SKILL_YEARS_PATTERNS:
        match = pattern.fullmatch(raw)
        if not match:
            continue
        skill = " ".join(match.group("skill").strip(" .?").split())
        if not skill or len(skill) > 120:
            return None
        return {
            "matched": True,
            "catalog_version": CATALOG_VERSION,
            "question_key": f"experience.skill_years.{_slug(skill)}",
            "question_family": "experience_skill",
            "answer_type": "number",
            "sensitivity_class": "NORMAL",
            "profile_fact_key": None,
            "autofill_default": False,
            "conditions": {"skill": skill},
            "canonical_concept": "experience.skill_years",
            "confidence": 1.0,
            "match_type": "dynamic_skill_years",
            "normalized_question": normalize_question(question),
        }
    return None


def match_question(
    question: str,
    *,
    minimum_score: float = 0.84,
    minimum_margin: float = 0.05,
) -> dict[str, Any]:
    """Map visible wording to one stable concept or fail closed."""
    raw = " ".join(str(question or "").split())
    if not raw or len(raw) > 2_000:
        return {"matched": False, "reason": "invalid_or_empty_question"}

    dynamic = _dynamic_skill_match(raw)
    if dynamic:
        return dynamic

    normalized = normalize_question(raw)
    scored: list[tuple[float, QuestionConcept, str]] = []
    for concept in CONCEPTS:
        for alias in concept.aliases:
            scored.append((_score(normalized, alias), concept, alias))
    scored.sort(key=lambda item: (-item[0], item[1].key, item[2]))
    top_score, top, top_alias = scored[0]
    second_score = next(
        (score for score, concept, _alias in scored if concept.key != top.key),
        0.0,
    )
    if top_score < float(minimum_score):
        return {
            "matched": False,
            "reason": "similarity_below_threshold",
            "confidence": top_score,
            "normalized_question": normalized,
        }
    if top_score < 1.0 and (top_score - second_score) < float(minimum_margin):
        return {
            "matched": False,
            "reason": "ambiguous_similarity",
            "confidence": top_score,
            "runner_up_confidence": second_score,
            "normalized_question": normalized,
        }
    return {
        "matched": True,
        "catalog_version": CATALOG_VERSION,
        "question_key": top.key,
        "question_family": top.family,
        "answer_type": top.answer_type,
        "sensitivity_class": top.sensitivity_class,
        "profile_fact_key": top.profile_fact_key,
        "autofill_default": top.autofill_default,
        "conditions": {},
        "canonical_concept": top.key,
        "confidence": top_score,
        "match_type": "exact" if top_score == 1.0 else "deterministic_similarity",
        "matched_alias": top_alias,
        "normalized_question": normalized,
    }


def requirement_from_question(
    question: str,
    *,
    required: bool = True,
) -> dict[str, Any]:
    """Produce the requirement shape expected by Application Plan V2."""
    matched = match_question(question)
    if not matched.get("matched"):
        normalized = normalize_question(question)
        return {
            "question_key": f"unknown.{_slug(normalized)[:120]}",
            "question_family": "unknown",
            "normalized_question": normalized or "Unknown application question",
            "required": bool(required),
            "profile_fact_key": None,
            "conditions": {},
            "sensitivity_class": "NORMAL",
            "catalog_match": matched,
        }
    return {
        "question_key": matched["question_key"],
        "question_family": matched["question_family"],
        "normalized_question": matched["normalized_question"],
        "required": bool(required),
        "profile_fact_key": matched["profile_fact_key"],
        "conditions": dict(matched.get("conditions") or {}),
        "sensitivity_class": matched["sensitivity_class"],
        "catalog_match": {
            "catalog_version": matched["catalog_version"],
            "canonical_concept": matched["canonical_concept"],
            "confidence": matched["confidence"],
            "match_type": matched["match_type"],
        },
    }


def catalog_projection() -> list[dict[str, Any]]:
    return [asdict(item) for item in CONCEPTS]


def catalog_metrics() -> dict[str, int | str]:
    return {
        "version": CATALOG_VERSION,
        "concept_count": len(CONCEPTS),
        "common_question_count": sum(len(item.aliases) for item in CONCEPTS),
    }
