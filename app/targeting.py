from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.database import get_connection, get_setting


RULES_ENGINE_VERSION = "canonical-targeting-v3.1"


class PrimaryCategory(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    DUPLICATE = "DUPLICATE"
    REJECT_ROLE = "REJECT_ROLE"
    REJECT_LOCATION = "REJECT_LOCATION"
    REJECT_HARD_REQUIREMENT = "REJECT_HARD_REQUIREMENT"
    REJECT_COMPANY = "REJECT_COMPANY"
    REJECT_OTHER_TARGETING = "REJECT_OTHER_TARGETING"


PRIMARY_CATEGORIES = tuple(value.value for value in PrimaryCategory)


US_STATES: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut",
    "DE": "delaware", "FL": "florida", "GA": "georgia", "HI": "hawaii",
    "ID": "idaho", "IL": "illinois", "IN": "indiana", "IA": "iowa",
    "KS": "kansas", "KY": "kentucky", "LA": "louisiana", "ME": "maine",
    "MD": "maryland", "MA": "massachusetts", "MI": "michigan",
    "MN": "minnesota", "MS": "mississippi", "MO": "missouri",
    "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico",
    "NY": "new york", "NC": "north carolina", "ND": "north dakota",
    "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas",
    "UT": "utah", "VT": "vermont", "VA": "virginia",
    "WA": "washington", "WV": "west virginia", "WI": "wisconsin",
    "WY": "wyoming", "DC": "district of columbia", "PR": "puerto rico",
    "VI": "u s virgin islands", "GU": "guam", "AS": "american samoa",
    "MP": "northern mariana islands",
}

US_CITY_HINTS = {
    "atlanta", "austin", "baltimore", "birmingham", "boston", "charlotte",
    "chicago", "cincinnati", "cleveland", "columbus", "dallas", "denver",
    "detroit", "houston", "indianapolis", "jacksonville", "kansas city",
    "las vegas", "los angeles", "memphis", "miami", "milwaukee",
    "minneapolis", "nashville", "new orleans", "new york", "new york city",
    "newark", "oakland", "oklahoma city", "orlando", "philadelphia",
    "phoenix", "pittsburgh", "portland", "raleigh", "richmond",
    "sacramento", "salt lake city", "san antonio", "san diego",
    "san francisco", "san jose", "seattle", "st louis", "tampa",
    "tucson", "washington dc",
}

FOREIGN_CITY_HINTS = {
    "amsterdam", "abu dhabi", "bangalore", "bengaluru", "barcelona",
    "berlin", "brussels", "budapest", "copenhagen", "delhi", "dubai",
    "dublin", "edinburgh", "glasgow", "helsinki", "hyderabad", "lisbon",
    "london", "madrid", "manchester", "melbourne", "mexico city", "milan",
    "mumbai", "munich", "oslo", "paris", "prague", "rome", "seoul",
    "singapore", "stockholm", "sydney", "tel aviv", "tokyo", "toronto",
    "vancouver", "vienna", "warsaw", "zurich",
}

US_COUNTRY_ALIASES = {
    "us", "u s", "usa", "u s a", "united states", "united states of america",
    "america",
}

GLOBAL_LOCATION_TERMS = {
    "global", "worldwide", "international", "anywhere", "work from anywhere",
    "remote worldwide", "location independent", "emea", "apac", "latam",
    "europe", "european union",
}

EXEMPT_PATH_TERMS = {
    "manual input", "telegram manual", "stored job n8n", "n8n callback",
    "force rerun", "resume", "application package", "user input",
}

PROTECTED_STATUSES = {
    "approved for n8n", "sent to n8n", "application ready", "n8n failed",
    "already applied", "hold", "held",
}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12,
}
YEAR_TOKEN = r"(?:\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)"
EXPERIENCE_RE = re.compile(
    rf"(?ix)\b(?P<low>{YEAR_TOKEN})\s*(?P<plus>\+|plus)?\s*"
    rf"(?:(?:-|–|—|to)\s*(?P<high>{YEAR_TOKEN})\s*)?years?\b"
)


def _year_number(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    return int(normalized) if normalized.isdigit() else NUMBER_WORDS.get(normalized)


def plain(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    text = re.sub(r"\bhrbp\b", "human resources business partner", text)
    text = re.sub(r"\bhris\b", "human resources information systems", text)
    text = re.sub(r"(?<![a-z0-9])hr(?![a-z0-9])", "human resources", text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


HOURLY_RATE_HR_RE = re.compile(
    r"(?ix)(?P<rate>(?:usd\s*)?[$€£]?\s*\d+(?:[.,]\d+)?\s*(?:/|\bper\b\s*)?)"
    r"(?<![a-z0-9])hr(?![a-z0-9])"
)


def _role_title_text(value: Any) -> str:
    """Mask compensation-unit ``hr`` before HR role abbreviation expansion."""
    text = str(value or "")
    return HOURLY_RATE_HR_RE.sub(
        lambda match: f"{match.group('rate')} hourly rate",
        text,
    )


def _clean_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            output.append(text)
    return output


def phrase_in(text: str, phrase: str) -> bool:
    normalized = plain(phrase)
    if not normalized:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return bool(re.search(pattern, plain(text)))


def _json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CanonicalRules:
    target_roles: tuple[str, ...]
    target_tracks: tuple[str, ...]
    hard_reject_keywords: tuple[str, ...]
    company_blacklist: tuple[str, ...]
    boosted_keywords: tuple[str, ...]
    company_watchlist: tuple[str, ...]
    role_families: tuple[dict[str, Any], ...]
    role_negative_contexts: tuple[str, ...]
    title_only_hard_rejects: tuple[str, ...]
    eligibility: dict[str, Any]
    experience_policy: dict[str, Any]
    location_plan: tuple[dict[str, Any], ...]
    rules_version: str
    rules_hash: str

    @property
    def matching_roles(self) -> tuple[str, ...]:
        return self.target_roles

    @property
    def rejected_keywords(self) -> tuple[str, ...]:
        return self.hard_reject_keywords


def _load_location_plan() -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(location_rules)")
        }
        purpose_sql = "rule_purpose" if "rule_purpose" in columns else "'preference' AS rule_purpose"
        rows = connection.execute(
            f"""
            SELECT id AS rule_id, location_name AS rule_name,
                   location_type AS rule_type, city, state, country,
                   remote_allowed, hybrid_allowed, onsite_allowed,
                   priority_weight, {purpose_sql}
            FROM location_rules
            WHERE COALESCE(is_active, 1) = 1
            ORDER BY priority_weight DESC, id
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def load_rules() -> CanonicalRules:
    targeting = get_setting("targeting", {}) or {}
    if not isinstance(targeting, dict):
        targeting = {}
    hard_rejects = targeting.get("hard_reject_keywords")
    if not isinstance(hard_rejects, list):
        hard_rejects = targeting.get("rejected_keywords") or []
    locations = _load_location_plan()
    rules_version = str(targeting.get("schema_version") or RULES_ENGINE_VERSION)
    hash_payload = {
        "targeting": targeting,
        "locations": locations,
        "engine": RULES_ENGINE_VERSION,
    }
    return CanonicalRules(
        target_roles=tuple(_clean_list(targeting.get("target_roles"))),
        target_tracks=tuple(_clean_list(targeting.get("target_tracks"))),
        hard_reject_keywords=tuple(_clean_list(hard_rejects)),
        company_blacklist=tuple(_clean_list(targeting.get("company_blacklist"))),
        boosted_keywords=tuple(_clean_list(targeting.get("boosted_keywords"))),
        company_watchlist=tuple(_clean_list(targeting.get("company_watchlist"))),
        role_families=tuple(
            dict(item) for item in (targeting.get("role_families") or [])
            if isinstance(item, Mapping)
        ),
        role_negative_contexts=tuple(_clean_list(targeting.get("role_negative_contexts"))),
        title_only_hard_rejects=tuple(_clean_list(targeting.get("title_only_hard_rejects"))),
        eligibility=dict(targeting.get("eligibility") or {}),
        experience_policy=dict(targeting.get("experience_policy") or {}),
        location_plan=tuple(locations),
        rules_version=rules_version,
        rules_hash=_json_hash(hash_payload),
    )


def _field_text(job: Mapping[str, Any], fields: Iterable[str]) -> str:
    values: list[str] = []
    for field in fields:
        value = job.get(field)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list, tuple, set)):
            values.append(json.dumps(value, ensure_ascii=False, default=str))
        else:
            values.append(str(value))
    return " \n ".join(values)


def _family_for_match(title: str, matched_role: str, rules: CanonicalRules) -> str:
    role_title = _role_title_text(title)
    candidates: list[tuple[int, str]] = []
    for family in rules.role_families:
        name = str(family.get("name") or "").strip()
        for phrase in _clean_list(family.get("title_phrases")):
            if phrase_in(role_title, phrase) or phrase_in(matched_role, phrase):
                candidates.append((len(plain(phrase)), name))
    return max(candidates, default=(0, matched_role), key=lambda item: item[0])[1]


def role_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> dict[str, Any]:
    title = str(job.get("title") or job.get("job_title") or job.get("role") or "").strip()
    if not title:
        return {"accepted": False, "reason": "missing_job_title", "evidence": []}

    role_title = _role_title_text(title)
    title_text = plain(role_title)
    description = _field_text(
        job,
        ("description_raw", "description", "responsibilities", "qualifications"),
    )
    combined = f"{title} \n {description}"
    negative_matches = [
        phrase for phrase in rules.role_negative_contexts
        if phrase_in(title, phrase)
    ]

    ambiguous_title_terms = ("sourcing", "recruit", "benefit", "compensation", "talent")
    if any(term in title_text for term in ambiguous_title_terms):
        negative_matches.extend(
            phrase for phrase in rules.role_negative_contexts
            if phrase_in(description, phrase)
        )
    negative_matches = list(dict.fromkeys(negative_matches))
    if negative_matches:
        return {
            "accepted": False,
            "reason": "negative_role_context",
            "evidence": negative_matches,
            "title": title,
        }

    matches: list[tuple[int, str, str]] = []
    for role in rules.target_roles:
        if phrase_in(role_title, role):
            matches.append((len(plain(role)), role, "configured_target_role"))

    for family in rules.role_families:
        family_name = str(family.get("name") or "").strip()
        for phrase in _clean_list(family.get("title_phrases")):
            if phrase_in(role_title, phrase):
                matches.append((len(plain(phrase)), phrase, family_name))

    # Tracks remain compatible but broad one-word policy terms never admit a job.
    for track in rules.target_tracks:
        if len(plain(track).split()) >= 2 and phrase_in(role_title, track):
            matches.append((len(plain(track)), track, "configured_target_track"))

    if not matches:
        return {
            "accepted": False,
            "reason": "title_not_in_configured_role_families",
            "evidence": [],
            "title": title,
        }

    _, matched, source = max(matches, key=lambda item: item[0])
    family = source if source not in {"configured_target_role", "configured_target_track"} else _family_for_match(title, matched, rules)
    return {
        "accepted": True,
        "reason": "configured_role_evidence",
        "matched_phrase": matched,
        "target_family": family,
        "match_source": source,
        "evidence": [{"field": "title", "text": title, "matched_phrase": matched}],
    }


def _sentences(value: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", item).strip()
        for item in re.split(r"(?:[\r\n]+|(?<=[.!?;])\s+|\s+[•·]\s+)", value)
        if item.strip()
    ]


def experience_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> list[dict[str, Any]]:
    policy = rules.experience_policy
    required_markers = tuple(plain(value) for value in _clean_list(policy.get("required_markers")))
    preferred_markers = tuple(plain(value) for value in _clean_list(policy.get("preferred_markers")))
    optional_markers = tuple(plain(value) for value in _clean_list(policy.get("optional_markers")))
    context_terms = tuple(plain(value) for value in _clean_list(policy.get("experience_context_terms")))
    excluded_contexts = tuple(
        plain(value) for value in _clean_list(policy.get("non_candidate_experience_contexts"))
    )
    fields = (
        ("qualifications", "REQUIRED"),
        ("preferred_qualifications", "PREFERRED"),
        ("description_raw", "UNKNOWN"),
        ("description", "UNKNOWN"),
        ("responsibilities", "UNKNOWN"),
        ("work_authorization", "UNKNOWN"),
    )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int | None, str]] = set()
    for field, field_default in fields:
        raw = str(job.get(field) or "")
        if not raw:
            continue
        for sentence in _sentences(raw):
            normalized_sentence = plain(sentence)
            for match in EXPERIENCE_RE.finditer(sentence):
                low = _year_number(match.group("low"))
                high = _year_number(match.group("high"))
                if low is None:
                    continue
                has_required = any(marker and phrase_in(normalized_sentence, marker) for marker in required_markers)
                has_preferred = any(marker and phrase_in(normalized_sentence, marker) for marker in preferred_markers)
                has_optional = any(marker and phrase_in(normalized_sentence, marker) for marker in optional_markers)
                matched_contexts = [
                    marker for marker in context_terms
                    if marker and phrase_in(normalized_sentence, marker)
                ]
                excluded_matches = [
                    marker for marker in excluded_contexts
                    if marker and phrase_in(normalized_sentence, marker)
                ]
                qualification_requirement = field == "qualifications" and has_required
                if excluded_matches or (not matched_contexts and not qualification_requirement):
                    classification = "UNKNOWN"
                elif has_optional:
                    classification = "OPTIONAL"
                elif has_required:
                    classification = "REQUIRED"
                elif has_preferred:
                    classification = "PREFERRED"
                else:
                    classification = field_default
                key = (field, low, high, sentence.casefold())
                if key in seen:
                    continue
                seen.add(key)
                output.append(
                    {
                        "field": field,
                        "classification": classification,
                        "minimum_years": low,
                        "maximum_years": high,
                        "evidence": sentence[:500],
                        "context_matches": matched_contexts,
                        "excluded_context_matches": excluded_matches,
                    }
                )
    return output


def _split_scope(raw: str) -> tuple[str, str]:
    if ":" in raw:
        prefix, value = raw.split(":", 1)
        if plain(prefix) in {"title", "company", "description", "any"}:
            return plain(prefix), value.strip()
    return "auto", raw.strip()


def hard_requirement_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> dict[str, Any]:
    experiences = experience_evidence(job, rules)
    try:
        threshold = int(rules.experience_policy.get("reject_required_min_years"))
    except (TypeError, ValueError):
        threshold = 999
    required_experience = [
        item for item in experiences
        if item["classification"] == "REQUIRED" and int(item["minimum_years"]) >= threshold
    ]

    title = str(job.get("title") or job.get("job_title") or "")
    company = str(job.get("company_name") or job.get("company") or "")
    description_fields = (
        "description_raw", "description", "qualifications", "preferred_qualifications",
        "responsibilities", "work_authorization", "employment_type", "salary_raw",
    )
    description = _field_text(job, description_fields)
    all_text = f"{title} \n {company} \n {description}"
    title_only = {plain(value) for value in rules.title_only_hard_rejects}
    phrase_matches: list[dict[str, Any]] = []

    configured_terms = list(rules.hard_reject_keywords)
    configured_plain = {plain(_split_scope(str(value))[1]) for value in configured_terms}
    configured_terms.extend(
        f"title:{value}"
        for value in rules.title_only_hard_rejects
        if plain(value) not in configured_plain
    )

    for raw in configured_terms:
        scope, value = _split_scope(str(raw))
        clean = plain(value)
        if not clean or EXPERIENCE_RE.search(value):
            continue
        if scope == "title" or (scope == "auto" and clean in title_only):
            haystack, field = title, "title"
        elif scope == "company":
            haystack, field = company, "company_name"
        elif scope == "description":
            haystack, field = description, "description"
        else:
            haystack, field = all_text, "any"
        if not phrase_in(haystack, value):
            continue

        if field in {"description", "any"} and len(clean.split()) == 1:
            snippets = [sentence for sentence in _sentences(description) if phrase_in(sentence, value)]
            accepted_snippet = None
            for snippet in snippets:
                normalized = plain(snippet)
                preferred = any(phrase_in(normalized, marker) for marker in rules.experience_policy.get("preferred_markers", []))
                optional = any(phrase_in(normalized, marker) for marker in rules.experience_policy.get("optional_markers", []))
                required = any(phrase_in(normalized, marker) for marker in rules.experience_policy.get("required_markers", []))
                if required and not optional:
                    accepted_snippet = snippet
                    break
                if not preferred and not optional and field == "any" and phrase_in(title, value):
                    accepted_snippet = title
                    field = "title"
                    break
            if accepted_snippet is None:
                continue
            evidence_text = accepted_snippet
        else:
            evidence_text = title if field == "title" else company if field == "company_name" else next(
                (sentence for sentence in _sentences(description) if phrase_in(sentence, value)),
                value,
            )
        phrase_matches.append(
            {"configured_term": raw, "field": field, "evidence": str(evidence_text)[:500]}
        )

    return {
        "rejected": bool(required_experience or phrase_matches),
        "required_experience": required_experience,
        "all_experience_evidence": experiences,
        "configured_phrase_matches": phrase_matches,
        "experience_threshold_years": threshold,
    }


@lru_cache(maxsize=1)
def _foreign_country_names() -> set[str]:
    names: set[str] = set()
    try:
        import pycountry

        for country in pycountry.countries:
            if str(country.alpha_2).upper() == "US":
                continue
            for attribute in ("name", "official_name", "common_name"):
                value = getattr(country, attribute, None)
                if value:
                    normalized = plain(value)
                    if normalized and normalized != "georgia":
                        names.add(normalized)
    except Exception:
        names.update({"canada", "mexico", "united kingdom", "germany", "france", "india"})
    names.update({"uk", "u k", "england", "scotland", "wales", "uae", "europe"})
    return names


def _term_matches(text: str, terms: Iterable[str]) -> list[str]:
    return sorted({term for term in terms if phrase_in(text, term)})


def _country_code(value: Any) -> str | None:
    normalized = plain(value)
    if not normalized:
        return None
    if normalized in US_COUNTRY_ALIASES:
        return "US"
    raw = str(value or "").strip().upper()
    if len(raw) == 2 and raw.isalpha():
        return raw
    try:
        import pycountry

        match = pycountry.countries.lookup(str(value))
        return str(match.alpha_2).upper()
    except Exception:
        return None


def _state_evidence(job: Mapping[str, Any], location: str) -> list[str]:
    output: list[str] = []
    raw_state = str(job.get("state") or "").strip()
    if raw_state.upper() in US_STATES:
        output.append(f"state:{raw_state.upper()}")
    normalized_location = plain(location)
    for code, name in US_STATES.items():
        if phrase_in(normalized_location, name):
            output.append(f"state_name:{name}")
        if re.search(r"(?i)(?:^|[\s,|(/-])" + re.escape(code) + r"(?:$|[\s,|)/-])", location):
            output.append(f"state_code:{code}")
    return list(dict.fromkeys(output))


def _arrangement(job: Mapping[str, Any]) -> str:
    text = plain(
        " ".join(
            str(job.get(key) or "")
            for key in ("remote_type", "work_mode", "workplace_type", "location_raw", "location")
        )
    )
    if "hybrid" in text:
        return "hybrid"
    if any(term in text for term in ("remote", "work from home", "wfh", "telecommute")):
        return "remote"
    return "onsite"


def location_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> dict[str, Any]:
    location = _field_text(job, ("location_raw", "location", "city", "state"))
    normalized_location = plain(location)
    raw_country_value = job.get("country_code") or job.get("country")
    provider_country_value = job.get("_provider_country_raw") or job.get("provider_country")
    raw_country = _country_code(raw_country_value)
    provider_country = _country_code(provider_country_value)
    allowed_countries = {
        str(value).strip().upper()
        for value in (rules.eligibility.get("country_codes") or [])
        if str(value).strip()
    }
    arrangement = _arrangement(job)
    state_matches = _state_evidence(job, location)
    explicit_us_terms = _term_matches(normalized_location, US_COUNTRY_ALIASES)
    us_city_matches = _term_matches(normalized_location, US_CITY_HINTS)
    foreign_country_matches = _term_matches(normalized_location, _foreign_country_names())
    foreign_city_matches = _term_matches(normalized_location, FOREIGN_CITY_HINTS)
    global_matches = _term_matches(normalized_location, GLOBAL_LOCATION_TERMS)
    parenthesized_codes = [
        match.group(1).upper()
        for match in re.finditer(r"\(([A-Za-z]{2})\)", location)
        if match.group(1).upper() not in US_STATES and match.group(1).upper() != "US"
    ]
    positive_us = bool(state_matches or explicit_us_terms)
    if not positive_us and us_city_matches and (raw_country == "US" or provider_country == "US"):
        positive_us = True

    evidence = {
        "location_raw": location,
        "raw_country": raw_country,
        "provider_country": provider_country,
        "state_evidence": state_matches,
        "us_country_terms": explicit_us_terms,
        "us_city_hints": us_city_matches,
        "foreign_country_terms": foreign_country_matches,
        "foreign_city_hints": foreign_city_matches,
        "global_terms": global_matches,
        "parenthesized_foreign_codes": parenthesized_codes,
        "arrangement": arrangement,
        "allowed_countries": sorted(allowed_countries),
    }

    if allowed_countries != {"US"}:
        return {"accepted": False, "reason": "unsupported_or_missing_country_policy", "evidence": evidence}
    if provider_country and provider_country != "US":
        return {"accepted": False, "reason": "provider_explicit_non_us_country", "evidence": evidence}
    if raw_country and raw_country != "US":
        return {"accepted": False, "reason": "explicit_non_us_country", "evidence": evidence}
    if parenthesized_codes:
        return {"accepted": False, "reason": "explicit_foreign_country_code", "evidence": evidence}
    if foreign_country_matches and not state_matches:
        return {"accepted": False, "reason": "explicit_foreign_country_text", "evidence": evidence}
    if foreign_city_matches and not state_matches and not explicit_us_terms:
        return {"accepted": False, "reason": "foreign_city_without_us_locality", "evidence": evidence}
    if global_matches and not positive_us:
        return {"accepted": False, "reason": "worldwide_or_global_without_us_eligibility", "evidence": evidence}
    if not positive_us and provider_country != "US" and raw_country != "US":
        return {"accepted": False, "reason": "country_unknown_fail_closed", "evidence": evidence}

    allowed_key = f"{arrangement}_allowed"
    if not bool(rules.eligibility.get(allowed_key, False)):
        return {"accepted": False, "reason": f"{arrangement}_not_allowed", "evidence": evidence}

    normalized_job = dict(job)
    normalized_job["country"] = "US"
    normalized_job["remote_type"] = arrangement.title() if arrangement != "onsite" else "On-site"
    return {
        "accepted": True,
        "reason": "confirmed_us_eligible",
        "evidence": evidence,
        "normalized_job": normalized_job,
    }


def preference_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> dict[str, Any]:
    location = plain(_field_text(job, ("location_raw", "location", "city", "state")))
    state = str(job.get("state") or "").strip().upper()
    arrangement = _arrangement(job)
    matches: list[dict[str, Any]] = []
    for rule in rules.location_plan:
        if plain(rule.get("rule_purpose")) != "preference":
            continue
        rule_type = plain(rule.get("rule_type"))
        rule_state = str(rule.get("state") or "").strip().upper()
        rule_city = plain(rule.get("city"))
        rule_name = plain(rule.get("rule_name"))
        matched = False
        if rule_type == "state" and rule_state and rule_state == state:
            matched = True
        elif rule_type == "city" and rule_city and phrase_in(location, rule_city):
            matched = True
        elif rule_type == "region" and rule_name and phrase_in(location, rule_name):
            matched = True
        if matched:
            matches.append(
                {
                    "rule_id": rule.get("rule_id"),
                    "rule_name": rule.get("rule_name"),
                    "weight": int(rule.get("priority_weight") or 0),
                    "arrangement": arrangement,
                }
            )
    return {"score": sum(item["weight"] for item in matches), "matches": matches}


def company_evidence(job: Mapping[str, Any], rules: CanonicalRules) -> dict[str, Any]:
    company = str(job.get("company_name") or job.get("company") or "").strip()
    normalized = plain(company)
    matches = [
        value for value in rules.company_blacklist
        if plain(value) and (phrase_in(normalized, value) or plain(value) == normalized)
    ]
    return {"rejected": bool(matches), "company": company, "matches": matches}


def evaluate_job(job: Mapping[str, Any], rules: CanonicalRules | None = None) -> dict[str, Any]:
    rules = rules or load_rules()
    source_job = dict(job)
    role = role_evidence(source_job, rules)
    location = location_evidence(source_job, rules)
    # Role rejection has the highest primary precedence. Avoid scanning and
    # repeatedly normalizing full descriptions for experience evidence when
    # the title/context has already conclusively rejected the job. Large ATS
    # boards are dominated by non-HR titles, so this keeps scheduled cycles
    # inside their bounded runtime without changing any primary outcome.
    hard = (
        hard_requirement_evidence(source_job, rules)
        if role.get("accepted")
        else {
            "rejected": False,
            "required_experience": [],
            "all_experience_evidence": [],
            "configured_phrase_matches": [],
            "experience_threshold_years": None,
            "evaluation_skipped": "primary_role_rejected",
        }
    )
    company = company_evidence(source_job, rules)
    preference = preference_evidence(source_job, rules)

    secondary: list[str] = []
    if not role.get("accepted"):
        secondary.append(str(role.get("reason")))
    if not location.get("accepted"):
        secondary.append(str(location.get("reason")))
    if hard.get("rejected"):
        secondary.append("hard_requirement")
    if company.get("rejected"):
        secondary.append("company_blacklisted")

    if not rules.target_roles and not rules.role_families:
        primary = PrimaryCategory.REJECT_OTHER_TARGETING
        reason = "targeting_configuration_empty"
    elif not role.get("accepted"):
        primary = PrimaryCategory.REJECT_ROLE
        reason = str(role.get("reason") or "role_not_targeted")
    elif not location.get("accepted"):
        primary = PrimaryCategory.REJECT_LOCATION
        reason = str(location.get("reason") or "location_not_targeted")
    elif hard.get("rejected"):
        primary = PrimaryCategory.REJECT_HARD_REQUIREMENT
        reason = "configured_hard_requirement"
    elif company.get("rejected"):
        primary = PrimaryCategory.REJECT_COMPANY
        reason = "company_blacklisted"
    else:
        primary = PrimaryCategory.ELIGIBLE
        reason = "canonical_targeting_match"

    normalized_job = dict(location.get("normalized_job") or source_job)
    normalized_job["target_track"] = role.get("target_family") or role.get("matched_phrase")
    normalized_job["preference_score"] = int(preference.get("score") or 0)
    normalized_job["_targeting_rules_hash"] = rules.rules_hash
    normalized_job["_targeting_rules_version"] = rules.rules_version
    normalized_job["_role_evidence"] = role
    normalized_job["_experience_evidence"] = hard.get("all_experience_evidence") or []
    normalized_job["_location_evidence"] = location
    normalized_job["_preference_evidence"] = preference

    return {
        "accepted": primary == PrimaryCategory.ELIGIBLE,
        "primary_category": primary.value,
        "reason": reason,
        "secondary_reasons": list(dict.fromkeys(secondary)),
        "matched_target_role": role.get("matched_phrase"),
        "target_family": role.get("target_family"),
        "role_match_reason": role.get("reason"),
        "role_evidence": role,
        "experience_evidence": hard.get("all_experience_evidence") or [],
        "hard_requirement_evidence": hard,
        "company_evidence": company,
        "location_evidence": location,
        "preference": preference,
        "normalized_job": normalized_job,
        "rules_version": rules.rules_version,
        "rules_hash": rules.rules_hash,
        "configuration_source": "SQLite settings + location_rules",
        "canonical_targeting_gate": True,
        "personal_rules_hardcoded": False,
    }


def canonical_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", ""))
    except ValueError:
        return plain(raw)


def within_run_identity(job: Mapping[str, Any]) -> str:
    company = plain(job.get("company_name") or job.get("company"))
    title = plain(job.get("title") or job.get("job_title"))
    location = plain(job.get("location_raw") or job.get("location"))
    requisition = plain(job.get("requisition_id") or job.get("ats_job_id") or job.get("external_id"))
    url = canonical_url(job.get("apply_url") or job.get("job_url") or job.get("url"))
    if company and requisition:
        material = ["requisition", company, requisition]
    elif url:
        material = ["url", url]
    else:
        material = ["semantic", company, title, location]
    return hashlib.sha256("\x1f".join(material).encode("utf-8")).hexdigest()


def _decision_row(run_id: str, job: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    source = str(job.get("source") or job.get("source_name") or "Unknown")
    return {
        "run_id": run_id,
        "source_name": source,
        "external_id": str(job.get("external_id") or job.get("ats_job_id") or job.get("requisition_id") or "")[:300],
        "job_identity": within_run_identity(job),
        "title": str(job.get("title") or job.get("job_title") or "")[:500],
        "company_name": str(job.get("company_name") or job.get("company") or "")[:500],
        "location_raw": str(job.get("location_raw") or job.get("location") or "")[:1000],
        "primary_category": str(decision.get("primary_category")),
        "secondary_reasons": list(decision.get("secondary_reasons") or []),
        "evidence": {
            "reason": decision.get("reason"),
            "role": decision.get("role_evidence"),
            "experience": decision.get("experience_evidence"),
            "hard_requirement": decision.get("hard_requirement_evidence"),
            "location": decision.get("location_evidence"),
            "preference": decision.get("preference"),
        },
        "rules_version": decision.get("rules_version"),
        "rules_hash": decision.get("rules_hash"),
        "query_name": str(job.get("_query_name") or ""),
        "role_family": str(job.get("_role_family") or decision.get("target_family") or ""),
    }


def filter_jobs(jobs: list[dict[str, Any]], rules: CanonicalRules | None = None) -> dict[str, Any]:
    rules = rules or load_rules()
    run_id = str(uuid.uuid4())
    decisions: list[dict[str, Any]] = []
    eligible_jobs: list[dict[str, Any]] = []
    seen: dict[str, int] = {}

    for original in jobs:
        decision = evaluate_job(original, rules)
        normalized = dict(decision.get("normalized_job") or original)
        if decision["primary_category"] == PrimaryCategory.ELIGIBLE:
            identity = within_run_identity(normalized)
            if identity in seen:
                decision = dict(decision)
                decision["accepted"] = False
                decision["primary_category"] = PrimaryCategory.DUPLICATE.value
                decision["reason"] = "duplicate_within_run"
                decision["duplicate_of_index"] = seen[identity]
                decision["secondary_reasons"] = list(
                    dict.fromkeys([*(decision.get("secondary_reasons") or []), "duplicate_within_run"])
                )
            else:
                seen[identity] = len(decisions)
                normalized["_targeting_run_id"] = run_id
                normalized["_matched_target_role"] = decision.get("matched_target_role")
                normalized["_role_match_reason"] = decision.get("role_match_reason")
                normalized["_targeting_decision"] = decision
                eligible_jobs.append(normalized)
        decisions.append({**decision, "normalized_job": normalized})

    counts = {category: 0 for category in PRIMARY_CATEGORIES}
    for decision in decisions:
        counts[str(decision["primary_category"])] += 1
    raw = len(jobs)
    accounted = sum(counts.values())
    delta = raw - accounted
    decision_rows = [
        _decision_row(run_id, job, decision)
        for job, decision in zip(jobs, decisions)
    ]
    rejection_samples = [
        {
            "title": row["title"],
            "company": row["company_name"],
            "primary_category": row["primary_category"],
            "reason": row["evidence"].get("reason"),
            "evidence": row["evidence"],
        }
        for row in decision_rows
        if row["primary_category"] not in {PrimaryCategory.ELIGIBLE.value, PrimaryCategory.DUPLICATE.value}
    ][:25]

    return {
        "run_id": run_id,
        "eligible_jobs": eligible_jobs,
        "raw_jobs_found": raw,
        "raw_normalized": raw,
        "unique_jobs_ready": counts[PrimaryCategory.ELIGIBLE.value],
        "eligible": counts[PrimaryCategory.ELIGIBLE.value],
        "duplicates_within_run": counts[PrimaryCategory.DUPLICATE.value],
        "duplicate": counts[PrimaryCategory.DUPLICATE.value],
        "excluded_by_role": counts[PrimaryCategory.REJECT_ROLE.value],
        "excluded_by_location": counts[PrimaryCategory.REJECT_LOCATION.value],
        "excluded_by_hard_reject": counts[PrimaryCategory.REJECT_HARD_REQUIREMENT.value],
        "excluded_by_company_blacklist": counts[PrimaryCategory.REJECT_COMPANY.value],
        "excluded_by_other_targeting": counts[PrimaryCategory.REJECT_OTHER_TARGETING.value],
        "reject_role": counts[PrimaryCategory.REJECT_ROLE.value],
        "reject_location": counts[PrimaryCategory.REJECT_LOCATION.value],
        "reject_hard_requirement": counts[PrimaryCategory.REJECT_HARD_REQUIREMENT.value],
        "reject_company": counts[PrimaryCategory.REJECT_COMPANY.value],
        "reject_other_targeting": counts[PrimaryCategory.REJECT_OTHER_TARGETING.value],
        "rejected_jobs": raw - counts[PrimaryCategory.ELIGIBLE.value] - counts[PrimaryCategory.DUPLICATE.value],
        "primary_counts": counts,
        "accounting_delta": delta,
        "targeting_rules_hash": rules.rules_hash,
        "targeting_rules_version": rules.rules_version,
        "configuration_source": "SQLite settings + location_rules",
        "canonical_targeting_gate": True,
        "dashboard_targeting_gate": True,
        "personal_rules_hardcoded": False,
        "rejection_samples": rejection_samples,
        "_decision_rows": decision_rows,
    }


def is_discovery_path(job: Mapping[str, Any], actor: str = "") -> bool:
    combined = plain(
        " ".join(
            [
                actor,
                str(job.get("entry_path") or ""),
                str(job.get("source") or ""),
                str(job.get("status") or ""),
            ]
        )
    )
    if any(phrase_in(combined, term) for term in EXEMPT_PATH_TERMS):
        return False
    if plain(job.get("status")) in PROTECTED_STATUSES:
        return False
    for key in ("manual", "is_manual", "manual_input"):
        value = job.get(key)
        if value is True or plain(value) in {"1", "true", "yes"}:
            return False
    return True


def configured_queries(rules: CanonicalRules | None = None) -> list[dict[str, str]]:
    rules = rules or load_rules()
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for family in rules.role_families:
        family_name = str(family.get("name") or "Unassigned")
        for query in _clean_list(family.get("queries")):
            key = plain(query)
            if key and key not in seen:
                seen.add(key)
                output.append({"family": family_name, "query": query})
    if not output:
        for role in rules.target_roles:
            key = plain(role)
            if key and key not in seen:
                seen.add(key)
                output.append({"family": "Configured roles", "query": role})
    return output


def self_test() -> dict[str, Any]:
    rules = load_rules()
    fixtures = [
        ("positive", {"title": "People Analytics Analyst", "company_name": "Example", "location_raw": "Austin, TX", "state": "TX", "country": "US"}, PrimaryCategory.ELIGIBLE),
        ("patient", {"title": "Patient Recruitment Coordinator", "company_name": "Clinic", "location_raw": "Boston, MA", "state": "MA", "country": "US"}, PrimaryCategory.REJECT_ROLE),
        ("procurement", {"title": "Strategic Sourcing Analyst", "company_name": "Example", "location_raw": "Dallas, TX", "state": "TX", "country": "US", "description_raw": "Procurement and suppliers"}, PrimaryCategory.REJECT_ROLE),
        ("preferred_experience", {"title": "HR Operations Analyst", "company_name": "Example", "location_raw": "Remote - United States", "country": "US", "description_raw": "Three years preferred but not required."}, PrimaryCategory.ELIGIBLE),
        ("required_experience", {"title": "HR Operations Analyst", "company_name": "Example", "location_raw": "Remote - United States", "country": "US", "qualifications": "Requires a minimum of 3 years of HR experience."}, PrimaryCategory.REJECT_HARD_REQUIREMENT),
        ("foreign", {"title": "Recruiting Coordinator", "company_name": "Example", "location_raw": "Berlin, Germany", "country": "DE"}, PrimaryCategory.REJECT_LOCATION),
        ("worldwide", {"title": "Recruiting Coordinator", "company_name": "Example", "location_raw": "Worldwide", "remote_type": "Remote"}, PrimaryCategory.REJECT_LOCATION),
    ]
    results = []
    for name, job, expected in fixtures:
        decision = evaluate_job(job, rules)
        actual = decision["primary_category"]
        results.append({"name": name, "expected": expected.value, "actual": actual, "passed": actual == expected.value})
    funnel = filter_jobs([job for _, job, _ in fixtures], rules)
    return {
        "success": all(item["passed"] for item in results) and funnel["accounting_delta"] == 0,
        "fixtures": results,
        "accounting_delta": funnel["accounting_delta"],
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2, ensure_ascii=False))
