#!/usr/bin/env python3
"""Aadil HR Hunter — U.S. Geography Final Boundary Guard V1.

The Dashboard location table remains authoritative. While the active Dashboard
country policy is U.S.-only, this module prevents automatic discovery jobs with
explicit foreign/global/unknown geography from being admitted merely because an
adapter defaulted country to ``US``.

No provider calls and no database writes are performed here.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any

HOME = Path.home()
PROJECT = Path(
    os.environ.get("AADIL_HR_HUNTER_PROJECT", HOME / "Aadil-HR-Hunter")
).expanduser().resolve()
DB_PATH = Path(
    os.environ.get(
        "AADIL_HR_HUNTER_HUNTER_DB",
        PROJECT / "data" / "hunter.db",
    )
).expanduser().resolve()

US_COUNTRY_ALIASES = {
    "us",
    "usa",
    "u.s.",
    "u.s.a.",
    "united states",
    "united states of america",
}

# Geography recognition only. These are NOT ranking or preference rules.
US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska",
    "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia",
}
US_STATE_ABBRS = set(US_STATES)
US_STATE_NAMES = set(US_STATES.values())

# Generic country vocabulary. Georgia is intentionally omitted because it is
# also a U.S. state; explicit country-code evidence still rejects country=GE.
FOREIGN_COUNTRY_NAMES = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina",
    "armenia", "australia", "austria", "azerbaijan", "bahamas", "bahrain",
    "bangladesh", "barbados", "belarus", "belgium", "belize", "benin",
    "bhutan", "bolivia", "bosnia and herzegovina", "botswana", "brazil",
    "brunei", "bulgaria", "burkina faso", "burundi", "cambodia", "cameroon",
    "canada", "cape verde", "chile", "china", "colombia", "costa rica",
    "croatia", "cuba", "cyprus", "czech republic", "czechia", "denmark",
    "dominican republic", "ecuador", "egypt", "el salvador", "estonia",
    "ethiopia", "fiji", "finland", "france", "germany", "ghana", "greece",
    "guatemala", "haiti", "honduras", "hong kong", "hungary", "iceland",
    "india", "indonesia", "iran", "iraq", "ireland", "israel", "italy",
    "jamaica", "japan", "jordan", "kazakhstan", "kenya", "kuwait", "latvia",
    "lebanon", "libya", "liechtenstein", "lithuania", "luxembourg", "malaysia",
    "maldives", "malta", "mexico", "moldova", "monaco", "mongolia",
    "montenegro", "morocco", "mozambique", "myanmar", "namibia", "nepal",
    "netherlands", "new zealand", "nicaragua", "nigeria", "north macedonia",
    "norway", "oman", "pakistan", "panama", "paraguay", "peru", "philippines",
    "poland", "portugal", "qatar", "romania", "russia", "russian federation",
    "rwanda", "saudi arabia", "senegal", "serbia", "singapore", "slovakia",
    "slovenia", "somalia", "south africa", "south korea", "spain", "sri lanka",
    "sweden", "switzerland", "syria", "taiwan", "tanzania", "thailand",
    "tunisia", "turkey", "türkiye", "uganda", "ukraine", "united arab emirates",
    "uae", "united kingdom", "england", "scotland", "wales", "uruguay",
    "uzbekistan", "venezuela", "vietnam", "zambia", "zimbabwe",
}

# High-confidence city hints used only if there is NO U.S. state/locality
# evidence. This preserves London, KY and Manchester, NH.
FOREIGN_CITY_NAMES = {
    "amsterdam", "abu dhabi", "bangalore", "bengaluru", "barcelona", "berlin",
    "brussels", "budapest", "copenhagen", "delhi", "dubai", "dublin",
    "edinburgh", "glasgow", "guadalajara", "helsinki", "holon", "hyderabad",
    "jerusalem", "lisbon", "london", "madrid", "manchester", "melbourne",
    "mexico city", "milan", "mumbai", "munich", "oslo", "paris", "prague",
    "rome", "rotterdam", "seoul", "singapore", "stockholm", "sydney",
    "tel aviv", "tokyo", "toronto", "vancouver", "vienna", "warsaw", "zurich",
    "ghent",
}

GLOBAL_LOCATION_TERMS = {
    "global",
    "worldwide",
    "international",
    "anywhere",
    "work from anywhere",
    "remote worldwide",
    "remote - worldwide",
    "location independent",
    "emea",
    "apac",
    "latam",
    "europe",
    "european union",
}

MANUAL_TERMS = {
    "telegram manual input",
    "manual input",
    "manual_job",
    "manual job",
    "dashboard manual",
    "user input",
    "user_input",
    "localhost manual",
}

_CACHE: dict[str, Any] = {"at": 0.0, "countries": None}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _token_pattern(term: str) -> re.Pattern[str]:
    # Alphabetic lookarounds prevent India from matching Indianapolis.
    return re.compile(r"(?i)(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])")


def _contains_term(text: str, term: str) -> bool:
    return bool(_token_pattern(term).search(text))


def _find_terms(text: str, terms: set[str]) -> list[str]:
    return sorted(term for term in terms if _contains_term(text, term))


def _is_us_country(value: Any) -> bool:
    return _norm(value) in US_COUNTRY_ALIASES


def _location_text(job: dict[str, Any]) -> str:
    return " | ".join(
        str(value)
        for value in (
            job.get("location_raw"),
            job.get("location"),
            job.get("city"),
            job.get("state"),
        )
        if value not in (None, "")
    )


def _raw_country_field(job: dict[str, Any]) -> str:
    return _norm(job.get("country_code") or job.get("country") or "")


def _provider_country(job: dict[str, Any]) -> str:
    for key in ("_provider_country_raw", "provider_country"):
        value = _norm(job.get(key))
        if value:
            return value
    return ""


def _state_field_us(job: dict[str, Any]) -> bool:
    state = _norm(job.get("state"))
    if not state:
        return False
    return state.upper() in US_STATE_ABBRS or state in US_STATE_NAMES


def _us_state_in_location(text: str) -> list[str]:
    found: list[str] = []
    for abbr in sorted(US_STATE_ABBRS):
        if re.search(
            r"(?i)(?:^|[\s,|(/-])" + re.escape(abbr) + r"(?:$|[\s,|)/-])",
            text,
        ):
            found.append(abbr)
    low = _norm(text)
    for name in sorted(US_STATE_NAMES):
        if _contains_term(low, name):
            found.append(name)
    return sorted(set(found))


def _explicit_us_text(text: str) -> bool:
    low = _norm(text)
    return any(_contains_term(low, term) for term in US_COUNTRY_ALIASES)


def _parenthesized_foreign_code(text: str) -> str | None:
    # Catch examples such as "Garching ... (DE)" generically. U.S. state
    # abbreviations are not treated as foreign here.
    for match in re.finditer(r"\(([A-Za-z]{2})\)", text):
        code = match.group(1).upper()
        if code not in US_STATE_ABBRS and code != "US":
            return code
    return None


def active_target_country_codes() -> set[str]:
    """Read active Dashboard country codes, cached for 30 seconds."""
    now = time.monotonic()
    if _CACHE["countries"] is not None and now - float(_CACHE["at"]) < 30.0:
        return set(_CACHE["countries"])

    countries: set[str] = set()
    if DB_PATH.exists():
        try:
            con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
            try:
                con.execute("PRAGMA query_only=ON")
                cols = {
                    str(row[1])
                    for row in con.execute('PRAGMA table_info("location_rules")')
                }
                if "country" in cols:
                    where = (
                        'WHERE COALESCE("is_active",1)=1'
                        if "is_active" in cols
                        else ""
                    )
                    for row in con.execute(
                        'SELECT DISTINCT "country" FROM "location_rules" ' + where
                    ):
                        value = _norm(row[0])
                        if value:
                            countries.add("US" if _is_us_country(value) else value.upper())
            finally:
                con.close()
        except Exception:
            # Fail back to existing Dashboard gate when the policy cannot be read.
            countries = set()

    _CACHE["at"] = now
    _CACHE["countries"] = set(countries)
    return countries


def us_only_dashboard_policy_active() -> bool:
    return active_target_country_codes() == {"US"}


def is_automatic_discovery_path(job: dict[str, Any], actor: str = "") -> bool:
    actor_low = _norm(actor)
    source_low = _norm(job.get("source") or job.get("source_name") or job.get("adapter"))
    combined = " | ".join([actor_low, source_low])

    if any(term in combined for term in MANUAL_TERMS):
        return False
    for key in ("manual", "is_manual", "manual_input"):
        value = job.get(key)
        if value is True or _norm(value) in {"1", "true", "yes"}:
            return False

    if any(token in actor_low for token in ("worker", "adapter", "jobspy", "source", "discovery")):
        return True
    if source_low and source_low not in {"manual", "unknown"}:
        return True
    return False


def evaluate_us_geography(job: dict[str, Any]) -> dict[str, Any]:
    """Return a value-blind U.S. geography decision for the active Dashboard policy."""
    if not us_only_dashboard_policy_active():
        return {
            "accepted": True,
            "reason": "defer_to_dashboard_non_us_only_policy",
            "guard_applied": False,
        }

    location = _location_text(job)
    low_location = _norm(location)
    raw_country = _raw_country_field(job)
    provider_country = _provider_country(job)

    state_field_us = _state_field_us(job)
    us_state_matches = _us_state_in_location(location)
    explicit_us = _explicit_us_text(location)
    positive_us_locality = bool(state_field_us or us_state_matches or explicit_us)

    # Provider-preserved country is strongest when present.
    if provider_country and not _is_us_country(provider_country):
        return {
            "accepted": False,
            "reason": "provider_explicit_non_us_country",
            "guard_applied": True,
            "country_evidence": provider_country.upper(),
        }

    # The normalized country field is still explicit evidence if it is non-US.
    # A country field of DE means Germany, not Delaware.
    if raw_country and not _is_us_country(raw_country):
        return {
            "accepted": False,
            "reason": "explicit_non_us_country",
            "guard_applied": True,
            "country_evidence": raw_country.upper(),
            "positive_us_locality": positive_us_locality,
        }

    parenthesized_code = _parenthesized_foreign_code(location)
    if parenthesized_code:
        return {
            "accepted": False,
            "reason": "explicit_foreign_country_code_in_location",
            "guard_applied": True,
            "country_evidence": parenthesized_code,
        }

    # Strong U.S. locality wins over ambiguous city vocabulary.
    if positive_us_locality:
        return {
            "accepted": True,
            "reason": "confirmed_us_locality",
            "guard_applied": True,
            "us_state_matches": us_state_matches,
            "explicit_us_text": explicit_us,
        }

    foreign_countries = _find_terms(low_location, FOREIGN_COUNTRY_NAMES)
    if foreign_countries:
        return {
            "accepted": False,
            "reason": "explicit_foreign_country_text",
            "guard_applied": True,
            "foreign_country_terms": foreign_countries,
        }

    foreign_cities = _find_terms(low_location, FOREIGN_CITY_NAMES)
    if foreign_cities:
        return {
            "accepted": False,
            "reason": "foreign_city_without_us_locality",
            "guard_applied": True,
            "foreign_city_terms": foreign_cities,
        }

    global_terms = _find_terms(low_location, GLOBAL_LOCATION_TERMS)
    if global_terms:
        return {
            "accepted": False,
            "reason": "global_or_worldwide_without_us_locality",
            "guard_applied": True,
            "global_terms": global_terms,
        }

    # Plain Remote + explicit U.S. country is valid. The global/anywhere cases
    # have already been rejected above.
    if _is_us_country(provider_country) or _is_us_country(raw_country):
        return {
            "accepted": True,
            "reason": "explicit_us_country",
            "guard_applied": True,
        }

    return {
        "accepted": False,
        "reason": "country_unknown_fail_closed",
        "guard_applied": True,
    }


def apply_guard_to_dashboard_result(
    job: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise TypeError("Dashboard targeting result must be a dictionary.")
    if not result.get("accepted"):
        return result

    decision = evaluate_us_geography(job)
    if decision.get("accepted"):
        merged = dict(result)
        merged["us_geography_guard_v1"] = decision
        return merged

    rejected = dict(result)
    rejected.update(
        {
            "accepted": False,
            "reason": "dashboard_targeting:us_geography:%s" % decision.get("reason"),
            "location_rejection_reason": decision.get("reason"),
            "us_geography_guard_v1": decision,
            "strict_dashboard_targeting": True,
        }
    )
    return rejected


def rejection_result(job: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    reason = "dashboard_targeting:us_geography:%s" % decision.get("reason")
    return {
        "job_id": None,
        "inserted": False,
        "duplicate_reason": None,
        "company": str(job.get("company_name") or job.get("company") or "Unknown Company"),
        "title": str(job.get("title") or "Unknown Position"),
        "hunter_score": 0,
        "match_label": "Rejected by dashboard targeting",
        "status": "rejected_by_dashboard_targeting",
        "dashboard_rejection_reason": reason,
        "location_rejection_reason": decision.get("reason"),
        "dashboard_gate": {
            "accepted": False,
            "reason": reason,
            "us_geography_guard_v1": decision,
            "strict_dashboard_targeting": True,
        },
        "strict_dashboard_targeting": True,
    }


def self_test() -> dict[str, Any]:
    old = dict(_CACHE)
    _CACHE["at"] = time.monotonic()
    _CACHE["countries"] = {"US"}
    try:
        fixtures = [
            ("austin", {"location_raw": "Austin, TX", "state": "TX", "country": "US"}, True),
            ("california", {"location_raw": "San Jose, CA", "state": "CA", "country": "US"}, True),
            ("florida", {"location_raw": "Tampa, FL", "state": "FL", "country": "US"}, True),
            ("indianapolis", {"location_raw": "Indianapolis, IN", "state": "IN", "country": "US"}, True),
            ("london_ky", {"location_raw": "London, KY", "state": "KY", "country": "US"}, True),
            ("manchester_nh", {"location_raw": "Manchester, NH", "state": "NH", "country": "US"}, True),
            ("remote_us", {"location_raw": "Remote", "country": "US", "remote_type": "Remote"}, True),
            ("germany_country", {"location_raw": "Garching b. München (DE)", "state": "DE", "country": "DE"}, False),
            ("london_fake_us", {"location_raw": "London", "country": "US", "remote_type": "hybrid"}, False),
            ("manchester_fake_us", {"location_raw": "Manchester M90 5EX (BOND)", "country": "US"}, False),
            ("berlin_fake_us", {"location_raw": "Berlin", "country": "US"}, False),
            ("paris_france_fake_us", {"location_raw": "Paris, France", "country": "US"}, False),
            ("global_fake_us", {"location_raw": "Global", "country": "US", "remote_type": "onsite"}, False),
            ("worldwide_fake_us", {"location_raw": "Worldwide", "country": "US", "remote_type": "Remote"}, False),
            ("unknown_remote", {"location_raw": "Remote", "country": None, "remote_type": "Remote"}, False),
        ]
        results = []
        for name, job, expected in fixtures:
            verdict = evaluate_us_geography(job)
            actual = bool(verdict.get("accepted"))
            results.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": actual,
                    "reason": verdict.get("reason"),
                    "passed": actual == expected,
                }
            )
        return {
            "success": all(item["passed"] for item in results),
            "results": results,
        }
    finally:
        _CACHE.clear()
        _CACHE.update(old)


if __name__ == "__main__":
    import json

    print(json.dumps(self_test(), indent=2))
