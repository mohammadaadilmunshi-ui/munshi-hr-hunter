from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PATCH_ID = "opt_us_nationwide_integrity_v1"
ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_HUNTER_DB = ROOT_DIR / "data" / "hunter.db"
DEFAULT_LOCK = ROOT_DIR / "data" / "diagnostics" / "private" / f"{PATCH_ID}.lock"


def _configured_n8n_db() -> Path:
    from app.runtime_config import n8n_database_path

    return n8n_database_path()

OPEN_QUEUE_STATUSES = {
    "pending",
    "queued",
    "ready",
    "retry",
    "reserved",
    "dispatching",
    "accepted",
    "running",
    "waiting",
    "processing",
}
TERMINAL_FAILURE_STATUSES = {
    "error",
    "failed",
    "crashed",
    "canceled",
    "cancelled",
}
TERMINAL_SUCCESS_STATUSES = {"success", "completed"}

# Generic ISO/US evidence. These are not location preferences and do not limit
# targeting to any state or city. They only recognize that a location is in the US.
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}

US_POSITIVE_PATTERNS = (
    r"\bunited states\b",
    r"\bu\.?s\.?a\.?\b",
    r"\bu\.?s\.?\b",
    r"\busa remote\b",
    r"\bremote[ ,\-/]*(?:within|in|across)?[ ,\-/]*(?:the )?united states\b",
)

# Explicit foreign evidence takes precedence over a provider's incorrect country=US default.
FOREIGN_PATTERNS = (
    r"\bunited kingdom\b", r"\bengland\b", r"\bscotland\b", r"\bwales\b",
    r"\bnorthern ireland\b", r"\bcanada\b", r"\bmexico\b", r"\bisrael\b",
    r"\bbelgium\b", r"\bnetherlands\b", r"\bgermany\b", r"\bfrance\b",
    r"\bspain\b", r"\bitaly\b", r"\bireland\b", r"\bportugal\b",
    r"\bpoland\b", r"\bsweden\b", r"\bnorway\b", r"\bdenmark\b",
    r"\bfinland\b", r"\bswitzerland\b", r"\baustria\b", r"\baustralia\b",
    r"\bnew zealand\b", r"\bindia\b", r"\bsingapore\b", r"\bjapan\b",
    r"\bchina\b", r"\bhong kong\b", r"\btaiwan\b", r"\bphilippines\b",
    r"\bmalaysia\b", r"\bindonesia\b", r"\bthailand\b", r"\bvietnam\b",
    r"\bbrazil\b", r"\bargentina\b", r"\bchile\b", r"\bcolombia\b",
    r"\bsouth africa\b", r"\bnigeria\b", r"\bkenya\b", r"\begypt\b",
    r"\bunited arab emirates\b", r"\buae\b", r"\bsaudi arabia\b",
    r"\bqatar\b", r"\bturkey\b", r"\bportishead\b", r"\bghent\b",
    r"\bamsterdam\b", r"\bholon\b", r"\bvancouver,?\s*bc\b",
    r"\btoronto\b", r"\bmontreal\b", r"\blondon,?\s*(?:uk|england)\b",
    r"\bemea\b", r"\bapac\b", r"\beurope\b",
)

TEXT_FIELDS = (
    "title", "job_title", "role", "description", "description_raw",
    "job_description", "full_job_description", "responsibilities",
    "qualifications", "preferred_qualifications", "preferred_skills",
    "skills_keywords", "manual_job_text",
)
LOCATION_FIELDS = (
    "location_raw", "location", "job_location", "city", "state", "country",
    "country_code", "region", "remote_location", "work_location",
)
ARRANGEMENT_FIELDS = (
    "remote_type", "work_arrangement", "workplace_type", "location_type",
    "remote", "is_remote", "hybrid", "is_hybrid",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_text() -> str:
    return _utc_now().isoformat()


def _parse_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parser in (
        lambda t: datetime.fromisoformat(t),
        lambda t: datetime.strptime(t.split(".")[0], "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            parsed = parser(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    if table not in _table_names(connection):
        return set()
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _connect(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(
            f"file:{path}?mode=ro", uri=True, timeout=30
        )
    else:
        connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def _setting(connection: sqlite3.Connection, key: str) -> dict[str, Any]:
    if "settings" not in _table_names(connection):
        return {}
    columns = _columns(connection, "settings")
    key_col = "setting_key" if "setting_key" in columns else "key" if "key" in columns else None
    value_col = "value_json" if "value_json" in columns else "value" if "value" in columns else None
    if not key_col or not value_col:
        return {}
    row = connection.execute(
        f'SELECT "{value_col}" FROM settings WHERE "{key_col}" = ?',
        (key,),
    ).fetchone()
    if not row:
        return {}
    try:
        parsed = json.loads(row[0] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _write_setting(
    connection: sqlite3.Connection,
    key: str,
    value: Mapping[str, Any],
) -> None:
    columns = _columns(connection, "settings")
    key_col = "setting_key" if "setting_key" in columns else "key" if "key" in columns else None
    value_col = "value_json" if "value_json" in columns else "value" if "value" in columns else None
    if not key_col or not value_col:
        raise RuntimeError("settings table does not expose a supported key/value schema")
    payload = json.dumps(dict(value), ensure_ascii=False, sort_keys=True)
    updated_col = "updated_at" if "updated_at" in columns else None
    existing = connection.execute(
        f'SELECT 1 FROM settings WHERE "{key_col}" = ?', (key,)
    ).fetchone()
    if existing:
        assignments = [f'"{value_col}" = ?']
        params: list[Any] = [payload]
        if updated_col:
            assignments.append(f'"{updated_col}" = CURRENT_TIMESTAMP')
        params.append(key)
        connection.execute(
            f'UPDATE settings SET {", ".join(assignments)} WHERE "{key_col}" = ?',
            params,
        )
    else:
        insert_cols = [key_col, value_col]
        values: list[Any] = [key, payload]
        if updated_col:
            insert_cols.append(updated_col)
            values.append(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        placeholders = ",".join("?" for _ in insert_cols)
        quoted = ",".join(f'"{name}"' for name in insert_cols)
        connection.execute(
            f"INSERT INTO settings ({quoted}) VALUES ({placeholders})", values
        )


def canonical_rules_hash(
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> str:
    path = Path(hunter_db)
    connection = _connect(path, readonly=True)
    try:
        payload = {
            "authorization": _setting(connection, "authorization"),
            "targeting": _setting(connection, "targeting"),
            "locations": [],
        }
        if "location_rules" in _table_names(connection):
            rows = connection.execute(
                "SELECT * FROM location_rules ORDER BY id"
            ).fetchall()
            payload["locations"] = [dict(row) for row in rows]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    finally:
        connection.close()


def install_dashboard_policy(
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    """Migrate the dashboard to OPT + nationwide US policy without local hardcoding."""
    path = Path(hunter_db)
    connection = _connect(path)
    changes: dict[str, Any] = {
        "authorization_changed": False,
        "hard_reject_keywords_synchronized": False,
        "us_country_rule_changed": False,
        "us_country_rule_inserted": False,
    }
    try:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Hunter DB quick_check failed")
        connection.execute("BEGIN IMMEDIATE")

        authorization = _setting(connection, "authorization")
        if not authorization:
            raise RuntimeError("Dashboard authorization setting is missing or malformed")
        before_authorization = dict(authorization)
        authorization["authorization_mode"] = "OPT"
        if authorization.get("opt_start_date"):
            authorization["opt_start_date"] = str(
                authorization["opt_start_date"]
            ).replace("/", "-")
        if authorization != before_authorization:
            _write_setting(connection, "authorization", authorization)
            changes["authorization_changed"] = True

        targeting = _setting(connection, "targeting")
        if not targeting:
            raise RuntimeError("Dashboard targeting setting is missing or malformed")
        before_targeting = dict(targeting)
        hard = targeting.get("hard_reject_keywords")
        rejected = targeting.get("rejected_keywords")
        if not isinstance(hard, list) or not [x for x in hard if str(x).strip()]:
            if isinstance(rejected, list) and [x for x in rejected if str(x).strip()]:
                targeting["hard_reject_keywords"] = [
                    str(x).strip() for x in rejected if str(x).strip()
                ]
        if targeting != before_targeting:
            _write_setting(connection, "targeting", targeting)
            changes["hard_reject_keywords_synchronized"] = True

        if "location_rules" not in _table_names(connection):
            raise RuntimeError("location_rules table is missing")
        columns = _columns(connection, "location_rules")
        country_rows = []
        if "country" in columns:
            country_rows = connection.execute(
                """
                SELECT * FROM location_rules
                WHERE upper(COALESCE(country, '')) IN ('US', 'USA')
                  AND lower(COALESCE(location_type, '')) = 'country'
                ORDER BY COALESCE(is_active, 0) DESC, id
                """
            ).fetchall()
        row = country_rows[0] if country_rows else None
        if row:
            assignments = []
            values: list[Any] = []
            for name, value in (
                ("is_active", 1),
                ("remote_allowed", 1),
                ("hybrid_allowed", 1),
                ("onsite_allowed", 1),
                ("country", "US"),
            ):
                if name in columns and row[name] != value:
                    assignments.append(f'"{name}" = ?')
                    values.append(value)
            if assignments:
                if "updated_at" in columns:
                    assignments.append('"updated_at" = CURRENT_TIMESTAMP')
                values.append(row["id"])
                connection.execute(
                    f'UPDATE location_rules SET {", ".join(assignments)} WHERE id = ?',
                    values,
                )
                changes["us_country_rule_changed"] = True
        else:
            defaults: dict[str, Any] = {
                "location_name": "United States",
                "location_type": "Country",
                "city": None,
                "state": None,
                "country": "US",
                "remote_allowed": 1,
                "hybrid_allowed": 1,
                "onsite_allowed": 1,
                "priority_weight": 15,
                "notes": "Nationwide US targeting controlled by dashboard",
                "is_active": 1,
            }
            insert_cols = [name for name in defaults if name in columns]
            if "created_at" in columns:
                defaults["created_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                insert_cols.append("created_at")
            if "updated_at" in columns:
                defaults["updated_at"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                insert_cols.append("updated_at")
            quoted = ",".join(f'"{name}"' for name in insert_cols)
            placeholders = ",".join("?" for _ in insert_cols)
            connection.execute(
                f"INSERT INTO location_rules ({quoted}) VALUES ({placeholders})",
                [defaults[name] for name in insert_cols],
            )
            changes["us_country_rule_inserted"] = True

        connection.commit()
        changes["rules_hash"] = canonical_rules_hash(path)
        return changes
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def dashboard_targeting_values(
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    path = Path(hunter_db)
    connection = _connect(path, readonly=True)
    try:
        targeting = _setting(connection, "targeting")
        hard = targeting.get("hard_reject_keywords")
        rejected = targeting.get("rejected_keywords")
        hard_terms = hard if isinstance(hard, list) else []
        if not hard_terms and isinstance(rejected, list):
            hard_terms = rejected
        return {
            "targeting": targeting,
            "hard_reject_keywords": [
                str(value).strip() for value in hard_terms if str(value).strip()
            ],
            "rules_hash": canonical_rules_hash(path),
        }
    finally:
        connection.close()


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(_flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(item) for item in value)
    return str(value)


def _normalized(text: Any) -> str:
    return " ".join(_flatten_text(text).casefold().split())


def _phrase_match(text: str, phrase: str) -> bool:
    clean_phrase = " ".join(str(phrase).casefold().split())
    if not clean_phrase:
        return False
    escaped = re.escape(clean_phrase).replace(r"\ ", r"\s+")
    # Word boundaries are used only when both ends are word characters.
    prefix = r"(?<!\w)" if clean_phrase[0].isalnum() else ""
    suffix = r"(?!\w)" if clean_phrase[-1].isalnum() else ""
    return bool(re.search(prefix + escaped + suffix, text, re.IGNORECASE))


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12,
}

_EXPERIENCE_TERM_RE = re.compile(
    r"(?i)\b(\d{1,2})\s*(?:\+|plus)?\s*years?\b"
)

_EXPERIENCE_REQUIREMENT_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        minimum(?:\s+of)?|
        at\s+least|
        requires?|
        required(?:\s+qualifications?)?|
        must\s+have|
        need(?:ed|s)?|
        possess(?:es|ing)?
    )?
    \s*
    (?P<low>\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)
    \s*
    (?:
        \+|
        plus|
        [-–—]\s*(?P<high>\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)|
        \s+to\s+(?P<high_to>\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)
    )?
    \s*years?
    (?:\s+of\s+(?:relevant\s+)?(?:professional\s+)?)?
    """
)


def _number_value(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).casefold().strip()
    if text.isdigit():
        return int(text)
    return _NUMBER_WORDS.get(text)


def _experience_thresholds(terms: Iterable[str]) -> set[int]:
    thresholds: set[int] = set()
    for term in terms:
        match = _EXPERIENCE_TERM_RE.search(str(term or ""))
        if match:
            thresholds.add(int(match.group(1)))
    return thresholds


def _required_experience_variant_matches(
    job: Mapping[str, Any],
    thresholds: set[int],
) -> list[str]:
    if not thresholds:
        return []

    raw = _flatten_text([job.get(field) for field in TEXT_FIELDS])
    matches: list[str] = []

    for match in _EXPERIENCE_REQUIREMENT_RE.finditer(raw):
        low = _number_value(match.group("low"))
        high = _number_value(match.group("high") or match.group("high_to"))
        if low is None:
            continue

        start = max(0, match.start() - 80)
        end = min(len(raw), match.end() + 120)
        context = " ".join(raw[start:end].casefold().split())

        optional_markers = (
            "preferred",
            "nice to have",
            "a plus",
            "plus but not required",
            "not required",
            "desired",
        )
        required_markers = (
            "minimum",
            "at least",
            "required",
            "requires",
            "must have",
            "need",
            "qualification",
        )
        is_optional = any(marker in context for marker in optional_markers)
        required_context = context.replace("not required", "")
        is_required = bool(match.group("prefix")) or any(
            marker in required_context for marker in required_markers
        )
        if is_optional and not is_required:
            continue

        # A range such as 4–6 years has a minimum requirement of four years.
        # Compare the lower bound to the configured threshold; using the upper
        # bound would incorrectly classify 4–6 as a 5+ year minimum.
        effective_minimum = low

        for threshold in sorted(thresholds):
            if effective_minimum >= threshold:
                label = f"{threshold}+ years (normalized requirement)"
                if label not in matches:
                    matches.append(label)

    return matches


_TITLE_ONLY_HARD_REJECT_TERMS = {
    "director",
    "vice president",
    "vp",
    "president",
    "chief",
    "head",
    "senior",
    "sr",
    "manager",
    "lead",
    "principal",
    "staff",
}


def _split_hard_reject_scope(raw_term: str) -> tuple[str, str]:
    raw = str(raw_term or "").strip()
    if not raw:
        return "auto", ""
    if ":" in raw:
        prefix, value = raw.split(":", 1)
        scope = prefix.strip().casefold()
        if scope in {"title", "company", "description", "any"}:
            return scope, value.strip()
    return "auto", raw


def matched_hard_rejects(
    job: Mapping[str, Any],
    terms: Iterable[str],
) -> list[str]:
    """Apply dashboard hard rejects with the same scope semantics as the gate.

    Seniority words default to title-only. Experience thresholds are evaluated
    through the normalized required-experience parser so descriptions such as
    "one to two years preferred, not required" do not become false hard rejects.
    """
    term_list = [str(term) for term in terms if str(term or "").strip()]

    title_text = _normalized(
        [job.get("title"), job.get("job_title"), job.get("role")]
    )
    company_text = _normalized(
        [job.get("company_name"), job.get("company")]
    )
    description_text = _normalized(
        [
            job.get("description"),
            job.get("description_raw"),
            job.get("job_description"),
            job.get("full_job_description"),
            job.get("responsibilities"),
            job.get("qualifications"),
            job.get("preferred_qualifications"),
            job.get("preferred_skills"),
            job.get("skills_keywords"),
            job.get("manual_job_text"),
            job.get("work_authorization"),
            job.get("employment_type"),
            job.get("salary_raw"),
        ]
    )
    all_text = " ".join(
        value for value in (title_text, company_text, description_text) if value
    )

    matches: list[str] = []
    for raw_term in term_list:
        scope, value = _split_hard_reject_scope(raw_term)
        clean_value = _normalized([value])
        if not clean_value:
            continue

        # Experience expressions are handled below with required/preferred
        # context. Do not broad-match them here.
        if _EXPERIENCE_TERM_RE.search(clean_value):
            continue

        if scope == "title":
            haystack = title_text
        elif scope == "company":
            haystack = company_text
        elif scope == "description":
            haystack = description_text
        elif scope == "any":
            haystack = all_text
        elif clean_value in _TITLE_ONLY_HARD_REJECT_TERMS:
            haystack = title_text
        else:
            haystack = all_text

        if _phrase_match(haystack, clean_value) and raw_term not in matches:
            matches.append(raw_term)

    thresholds = _experience_thresholds(term_list)
    for label in _required_experience_variant_matches(job, thresholds):
        if label not in matches:
            matches.append(label)
    return matches


def arrangement_for_job(job: Mapping[str, Any]) -> str:
    if job.get("is_hybrid") is True or job.get("hybrid") is True:
        return "hybrid"
    if job.get("is_remote") is True or job.get("remote") is True:
        return "remote"
    raw = _normalized([job.get(field) for field in ARRANGEMENT_FIELDS])
    if any(token in raw for token in ("hybrid", "partially remote")):
        return "hybrid"
    if any(token in raw for token in ("remote", "work from home", "wfh", "telecommute")):
        return "remote"
    return "onsite"


def _location_text(job: Mapping[str, Any]) -> str:
    return _normalized([job.get(field) for field in LOCATION_FIELDS])


def explicit_foreign_evidence(job: Mapping[str, Any]) -> list[str]:
    text = _location_text(job)
    evidence = [pattern for pattern in FOREIGN_PATTERNS if re.search(pattern, text, re.I)]
    country = str(job.get("country") or job.get("country_code") or "").strip().upper()
    if country and country not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        evidence.append(f"country:{country}")
    return evidence


def us_location_evidence(job: Mapping[str, Any]) -> list[str]:
    # Do not let a provider's country=US default manufacture textual evidence.
    # Country is recorded separately; strong evidence must come from actual
    # location/state fields.
    textual_fields = [
        field for field in LOCATION_FIELDS
        if field not in {"country", "country_code"}
    ]
    text = _normalized([job.get(field) for field in textual_fields])
    evidence: list[str] = []
    country = str(job.get("country") or job.get("country_code") or "").strip().upper()
    if country in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        evidence.append(f"country:{country}")
    for pattern in US_POSITIVE_PATTERNS:
        if re.search(pattern, text, re.I):
            evidence.append(pattern)
    state = str(job.get("state") or "").strip().upper()
    if state in US_STATE_CODES:
        evidence.append(f"state:{state}")
    # Detect standard city/state text such as Austin, TX or California, USA.
    tokens = re.findall(
        r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])",
        _flatten_text([job.get(f) for f in textual_fields]),
    )
    for token in tokens:
        if token in US_STATE_CODES:
            evidence.append(f"text_state:{token}")
    return list(dict.fromkeys(evidence))


def active_us_arrangements(
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, bool]:
    path = Path(hunter_db)
    connection = _connect(path, readonly=True)
    try:
        if "location_rules" not in _table_names(connection):
            return {"remote": False, "hybrid": False, "onsite": False}
        columns = _columns(connection, "location_rules")
        required = {"country", "location_type"}
        if not required.issubset(columns):
            return {"remote": False, "hybrid": False, "onsite": False}
        row = connection.execute(
            """
            SELECT * FROM location_rules
            WHERE upper(COALESCE(country, '')) IN ('US', 'USA')
              AND lower(COALESCE(location_type, '')) = 'country'
              AND COALESCE(is_active, 1) = 1
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return {"remote": False, "hybrid": False, "onsite": False}
        return {
            "remote": bool(row["remote_allowed"]) if "remote_allowed" in columns else False,
            "hybrid": bool(row["hybrid_allowed"]) if "hybrid_allowed" in columns else False,
            "onsite": bool(row["onsite_allowed"]) if "onsite_allowed" in columns else False,
        }
    finally:
        connection.close()


def nationwide_location_decision(
    job: Mapping[str, Any],
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    foreign = explicit_foreign_evidence(job)
    us = us_location_evidence(job)
    arrangement = arrangement_for_job(job)
    allowed = active_us_arrangements(hunter_db)
    strong_us = [item for item in us if not str(item).startswith("country:")]
    # A provider-supplied country=US is not enough to override explicit foreign
    # text. A real U.S. state or explicit United States text is enough for a
    # legitimate multi-location posting that is available inside the U.S.
    if foreign and not strong_us:
        return {
            "accepted": False,
            "reason": "country_evidence_conflict",
            "arrangement": arrangement,
            "foreign_evidence": foreign,
            "us_evidence": us,
            "arrangement_allowed": allowed.get(arrangement, False),
        }
    if not us:
        return {
            "accepted": False,
            "reason": "country_unknown_fail_closed",
            "arrangement": arrangement,
            "foreign_evidence": [],
            "us_evidence": [],
            "arrangement_allowed": allowed.get(arrangement, False),
        }
    if not allowed.get(arrangement, False):
        return {
            "accepted": False,
            "reason": f"dashboard_{arrangement}_disabled",
            "arrangement": arrangement,
            "foreign_evidence": [],
            "us_evidence": us,
            "arrangement_allowed": False,
        }
    return {
        "accepted": True,
        "reason": "dashboard_nationwide_us_match",
        "arrangement": arrangement,
        "foreign_evidence": [],
        "us_evidence": us,
        "arrangement_allowed": True,
    }


def evaluate_with_dashboard_policy(
    job: Mapping[str, Any],
    original_evaluator: Any,
    *,
    rules: Any = None,
    require_location: bool = True,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    """Use the existing role/company gate, then apply dynamic nationwide-US location policy."""
    try:
        base = original_evaluator(dict(job), rules=rules, require_location=False)
    except TypeError:
        try:
            base = original_evaluator(dict(job), rules=rules)
        except TypeError:
            base = original_evaluator(dict(job))
    if not isinstance(base, dict):
        raise TypeError("Existing dashboard evaluator returned a non-dictionary result")

    values = dashboard_targeting_values(hunter_db)
    hard_matches = matched_hard_rejects(job, values["hard_reject_keywords"])
    if hard_matches:
        normalized = dict(base)
        normalized.update(
            {
                "accepted": False,
                "reason": "dashboard_hard_reject_keyword",
                "hard_reject_matches": hard_matches,
                "configuration_source": "SQLite dashboard",
                "dashboard_targeting_gate": True,
                "personal_rules_hardcoded": False,
                "targeting_rules_hash": values["rules_hash"],
                "rules_hash": values["rules_hash"],
            }
        )
        return normalized

    if not base.get("accepted"):
        normalized = dict(base)
        normalized.setdefault("configuration_source", "SQLite dashboard")
        normalized.setdefault("dashboard_targeting_gate", True)
        normalized.setdefault("personal_rules_hardcoded", False)
        normalized.setdefault("targeting_rules_hash", values["rules_hash"])
        normalized.setdefault("rules_hash", values["rules_hash"])
        return normalized

    normalized = dict(base)
    if require_location:
        location = nationwide_location_decision(job, hunter_db)
        normalized.update(location)
        if not location["accepted"]:
            normalized["reason"] = f"dashboard_targeting:{location['reason']}"
    normalized.update(
        {
            "configuration_source": "SQLite dashboard",
            "dashboard_targeting_gate": True,
            "personal_rules_hardcoded": False,
            "targeting_rules_hash": values["rules_hash"],
            "rules_hash": values["rules_hash"],
            "strict_dashboard_targeting": True,
            "nationwide_us_policy": True,
        }
    )
    return normalized


def filter_with_dashboard_policy(
    jobs: Sequence[Mapping[str, Any]],
    original_filter: Any,
    evaluator: Any,
    *,
    rules: Any = None,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    try:
        original = original_filter(list(jobs), rules=rules)
    except TypeError:
        original = original_filter(list(jobs))
    result = dict(original) if isinstance(original, dict) else {}
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts = {
        "excluded_by_role": 0,
        "excluded_by_location": 0,
        "excluded_by_hard_reject": 0,
        "excluded_by_company_blacklist": 0,
    }
    for raw in jobs:
        decision = evaluator(dict(raw), rules=rules, require_location=True)
        item = dict(raw)
        item["dashboard_targeting"] = decision
        item["targeting_rules_hash"] = decision.get("targeting_rules_hash")
        if decision.get("accepted"):
            eligible.append(item)
            continue
        rejected.append(item)
        reason = str(decision.get("reason") or "").casefold()
        if "hard_reject" in reason:
            counts["excluded_by_hard_reject"] += 1
        elif "company" in reason or "blacklist" in reason:
            counts["excluded_by_company_blacklist"] += 1
        elif "location" in reason or "country" in reason or "remote" in reason or "hybrid" in reason or "onsite" in reason:
            counts["excluded_by_location"] += 1
        else:
            counts["excluded_by_role"] += 1
    result.update(counts)
    result.update(
        {
            "configuration_source": "SQLite dashboard",
            "dashboard_targeting_gate": True,
            "personal_rules_hardcoded": False,
            "strict_dashboard_targeting": True,
            "nationwide_us_policy": True,
            "targeting_rules_hash": canonical_rules_hash(hunter_db),
            "raw_jobs_found": len(jobs),
            "eligible_jobs": eligible,
            "unique_jobs_ready": len(eligible),
            "rejected_jobs": rejected,
        }
    )
    return result


def reclassify_explicit_foreign_jobs(
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    """Do not delete history; quarantine only found jobs with explicit foreign evidence."""
    path = Path(hunter_db)
    connection = _connect(path)
    changed: list[dict[str, Any]] = []
    try:
        if "jobs" not in _table_names(connection):
            return {"changed": []}
        columns = _columns(connection, "jobs")
        required = {"id", "status"}
        if not required.issubset(columns):
            return {"changed": []}
        select_cols = [name for name in ("id", *LOCATION_FIELDS, "status", "hard_rejection_reason") if name in columns]
        quoted_select_cols = ", ".join(f'"{name}"' for name in select_cols)
        rows = connection.execute(
            f"SELECT {quoted_select_cols} FROM jobs "
            "WHERE lower(COALESCE(status, '')) IN ('found','eligible','queued','ready')"
        ).fetchall()
        connection.execute("BEGIN IMMEDIATE")
        for row in rows:
            item = dict(row)
            foreign = explicit_foreign_evidence(item)
            if not foreign:
                continue
            assignments = ['"status" = ?']
            values: list[Any] = ["rejected_by_dashboard_targeting"]
            if "hard_rejection_reason" in columns:
                assignments.append('"hard_rejection_reason" = ?')
                values.append("dashboard_targeting:country_evidence_conflict")
            if "updated_at" in columns:
                assignments.append('"updated_at" = CURRENT_TIMESTAMP')
            values.append(item["id"])
            connection.execute(
                f'UPDATE jobs SET {", ".join(assignments)} WHERE id = ?', values
            )
            changed.append(
                {
                    "id": item["id"],
                    "old_status": item.get("status"),
                    "old_hard_rejection_reason": item.get("hard_rejection_reason"),
                    "foreign_evidence": foreign,
                }
            )
        connection.commit()
        return {"changed": changed}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _workflow_id_from_settings(hunter: sqlite3.Connection) -> str | None:
    runtime = _setting(hunter, "runtime")
    target_name = str(runtime.get("workflow_target") or "").strip()
    n8n_path = _configured_n8n_db()
    if not target_name or not n8n_path.exists():
        return None
    try:
        n8n = _connect(n8n_path, readonly=True)
        try:
            if "workflow_entity" not in _table_names(n8n):
                return None
            columns = _columns(n8n, "workflow_entity")
            if not {"id", "name"}.issubset(columns):
                return None
            row = n8n.execute(
                "SELECT id FROM workflow_entity WHERE name = ? ORDER BY updatedAt DESC LIMIT 1",
                (target_name,),
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            n8n.close()
    except sqlite3.Error:
        return None


def _candidate_workflow_id(
    hunter: sqlite3.Connection,
    n8n: sqlite3.Connection,
) -> str | None:
    if "n8n_results" in _table_names(hunter):
        result_cols = _columns(hunter, "n8n_results")
        if "execution_id" in result_cols and "execution_entity" in _table_names(n8n):
            rows = hunter.execute(
                "SELECT execution_id FROM n8n_results WHERE execution_id IS NOT NULL ORDER BY id DESC LIMIT 20"
            ).fetchall()
            for row in rows:
                execution_id = row[0]
                found = n8n.execute(
                    "SELECT workflowId FROM execution_entity WHERE id = ? LIMIT 1",
                    (execution_id,),
                ).fetchone()
                if found and found[0] not in (None, ""):
                    return str(found[0])

    runtime = _setting(hunter, "runtime")
    target_name = str(runtime.get("workflow_target") or "").strip()
    if target_name and "workflow_entity" in _table_names(n8n):
        columns = _columns(n8n, "workflow_entity")
        if {"id", "name"}.issubset(columns):
            order = "updatedAt DESC" if "updatedAt" in columns else "rowid DESC"
            row = n8n.execute(
                f"SELECT id FROM workflow_entity WHERE name = ? ORDER BY {order} LIMIT 1",
                (target_name,),
            ).fetchone()
            if row and row[0] not in (None, ""):
                return str(row[0])
    return None


def find_webhook_execution_for_queue(
    queue: Mapping[str, Any],
    *,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
    n8n_db: Path | str | None = None,
    workflow_id: str | None = None,
) -> dict[str, Any] | None:
    """Match the primary webhook execution, never n8n's mode=error handler run."""
    n8n_path = Path(n8n_db) if n8n_db is not None else _configured_n8n_db()
    hunter_path = Path(hunter_db)
    if not n8n_path.exists() or not hunter_path.exists():
        return None
    accepted = _parse_time(
        queue.get("accepted_at") or queue.get("reserved_at") or queue.get("queued_at")
    )
    if accepted is None:
        return None
    floor = accepted - timedelta(seconds=60)
    ceiling = accepted + timedelta(minutes=30)
    hunter = _connect(hunter_path, readonly=True)
    n8n = _connect(n8n_path, readonly=True)
    try:
        if "execution_entity" not in _table_names(n8n):
            return None
        columns = _columns(n8n, "execution_entity")
        selected = [
            name for name in (
                "id", "workflowId", "mode", "status", "startedAt", "stoppedAt", "finished"
            ) if name in columns
        ]
        if not {"id", "startedAt"}.issubset(selected):
            return None
        resolved_workflow = workflow_id or _candidate_workflow_id(hunter, n8n)
        conditions: list[str] = []
        params: list[Any] = []
        if "mode" in columns:
            conditions.append("lower(COALESCE(mode, '')) = 'webhook'")
        if resolved_workflow and "workflowId" in columns:
            conditions.append("workflowId = ?")
            params.append(resolved_workflow)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        quoted_selected = ", ".join(f'"{name}"' for name in selected)
        rows = n8n.execute(
            f"SELECT {quoted_selected} "
            f'FROM execution_entity{where} ORDER BY CAST(id AS INTEGER) DESC LIMIT 100',
            params,
        ).fetchall()
        candidates: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            item = dict(row)
            mode = str(item.get("mode") or "").casefold()
            if mode and mode != "webhook":
                continue
            started = _parse_time(item.get("startedAt"))
            if started is None or started < floor or started > ceiling:
                continue
            distance = abs((started - accepted).total_seconds())
            candidates.append((distance, item))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (pair[0], int(pair[1].get("id") or 0)))
        return candidates[0][1]
    finally:
        hunter.close()
        n8n.close()


def _progress_execution_id(
    hunter: sqlite3.Connection, queue_id: int
) -> int | None:
    if "telegram_n8n_progress" not in _table_names(hunter):
        return None
    columns = _columns(hunter, "telegram_n8n_progress")
    if not {"queue_id", "execution_id"}.issubset(columns):
        return None
    row = hunter.execute(
        "SELECT execution_id FROM telegram_n8n_progress WHERE queue_id = ? LIMIT 1",
        (queue_id,),
    ).fetchone()
    return int(row[0]) if row and row[0] not in (None, "") else None


def _result_exists(hunter: sqlite3.Connection, job_id: int, queue_id: int) -> bool:
    if "n8n_callback_receipts" in _table_names(hunter):
        receipt_columns = _columns(hunter, "n8n_callback_receipts")
        if "queue_id" in receipt_columns:
            row = hunter.execute(
                "SELECT 1 FROM n8n_callback_receipts WHERE queue_id = ? LIMIT 1",
                (queue_id,),
            ).fetchone()
            return bool(row)
    if "n8n_results" not in _table_names(hunter):
        return False
    columns = _columns(hunter, "n8n_results")
    clauses = []
    params: list[Any] = []
    if "queue_id" in columns:
        clauses.append("queue_id = ?")
        params.append(queue_id)
    elif "job_id" in columns:
        # Legacy databases without callback receipts or queue identity can
        # only fall back to job identity. Current databases always take the
        # exact receipt path above.
        clauses.append("job_id = ?")
        params.append(job_id)
    if not clauses:
        return False
    row = hunter.execute(
        f"SELECT 1 FROM n8n_results WHERE {' AND '.join(clauses)} LIMIT 1", params
    ).fetchone()
    return bool(row)


def _execution_by_id(n8n: sqlite3.Connection, execution_id: int) -> dict[str, Any] | None:
    columns = _columns(n8n, "execution_entity")
    selected = [
        name for name in (
            "id", "workflowId", "mode", "status", "startedAt", "stoppedAt", "finished"
        ) if name in columns
    ]
    if "id" not in selected:
        return None
    quoted_selected = ", ".join(f'"{name}"' for name in selected)
    row = n8n.execute(
        f"SELECT {quoted_selected} "
        "FROM execution_entity WHERE id = ? LIMIT 1",
        (execution_id,),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    if str(item.get("mode") or "").casefold() == "error":
        return None
    return item


def _save_execution_id(
    hunter: sqlite3.Connection,
    queue_id: int,
    execution_id: int,
) -> None:
    if "telegram_n8n_progress" not in _table_names(hunter):
        return
    columns = _columns(hunter, "telegram_n8n_progress")
    if not {"queue_id", "execution_id"}.issubset(columns):
        return
    assignments = ['"execution_id" = ?']
    if "run_status" in columns:
        assignments.append("\"run_status\" = CASE WHEN lower(COALESCE(run_status,'')) IN ('created','registered','waiting') THEN 'running' ELSE run_status END")
    if "updated_at" in columns:
        assignments.append('"updated_at" = CURRENT_TIMESTAMP')
    hunter.execute(
        f'UPDATE telegram_n8n_progress SET {", ".join(assignments)} WHERE queue_id = ?',
        (execution_id, queue_id),
    )


def _terminalize_queue(
    hunter: sqlite3.Connection,
    *,
    queue_id: int,
    job_id: int,
    execution_id: int | None,
    error_code: str,
) -> bool:
    columns = _columns(hunter, "n8n_dispatch_queue")
    status_col = "queue_status" if "queue_status" in columns else "status" if "status" in columns else None
    if not status_col:
        return False
    assignments = [f'"{status_col}" = ?']
    values: list[Any] = ["failed"]
    if "last_error" in columns:
        assignments.append('"last_error" = ?')
        values.append(error_code)
    if "completed_at" in columns:
        assignments.append('"completed_at" = CURRENT_TIMESTAMP')
    if "updated_at" in columns:
        assignments.append('"updated_at" = CURRENT_TIMESTAMP')
    placeholders = ",".join("?" for _ in OPEN_QUEUE_STATUSES)
    values.extend([queue_id, *sorted(OPEN_QUEUE_STATUSES)])
    cursor = hunter.execute(
        f'UPDATE n8n_dispatch_queue SET {", ".join(assignments)} '
        f'WHERE id = ? AND lower(COALESCE("{status_col}",\'\')) IN ({placeholders})',
        values,
    )
    if cursor.rowcount <= 0:
        return False

    if "telegram_n8n_progress" in _table_names(hunter):
        progress_cols = _columns(hunter, "telegram_n8n_progress")
        progress_assignments = []
        progress_values: list[Any] = []
        for name, value in (
            ("execution_id", execution_id),
            ("run_status", "failed"),
            ("error_message", error_code),
        ):
            if name in progress_cols:
                progress_assignments.append(f'"{name}" = ?')
                progress_values.append(value)
        if "completed_at" in progress_cols:
            progress_assignments.append('"completed_at" = CURRENT_TIMESTAMP')
        if "updated_at" in progress_cols:
            progress_assignments.append('"updated_at" = CURRENT_TIMESTAMP')
        if progress_assignments and "queue_id" in progress_cols:
            progress_values.append(queue_id)
            hunter.execute(
                f'UPDATE telegram_n8n_progress SET {", ".join(progress_assignments)} WHERE queue_id = ?',
                progress_values,
            )

    if "events" in _table_names(hunter):
        event_cols = _columns(hunter, "events")
        data = {
            "job_id": job_id,
            "event_type": "n8n_queue_auto_terminalized",
            "actor": PATCH_ID,
            "event_status": "failed",
            "payload_json": json.dumps(
                {
                    "queue_id": queue_id,
                    "execution_id": execution_id,
                    "error_code": error_code,
                },
                sort_keys=True,
            ),
        }
        insert_cols = [name for name in data if name in event_cols]
        if "created_at" in event_cols:
            data["created_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            insert_cols.append("created_at")
        if insert_cols:
            quoted = ",".join(f'"{name}"' for name in insert_cols)
            placeholders = ",".join("?" for _ in insert_cols)
            hunter.execute(
                f"INSERT INTO events ({quoted}) VALUES ({placeholders})",
                [data[name] for name in insert_cols],
            )
    return True


@contextlib.contextmanager
def _process_lock(lock_path: Path = DEFAULT_LOCK):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(lock_path.parent, 0o700)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reconcile_n8n_queue(
    *,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
    n8n_db: Path | str | None = None,
    no_execution_grace_seconds: int | None = None,
    callback_grace_seconds: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Idempotently terminalize callback-less/failed primary webhook executions."""
    hunter_path = Path(hunter_db)
    from app.runtime_config import downstream_int

    n8n_path = Path(n8n_db) if n8n_db is not None else _configured_n8n_db()
    if no_execution_grace_seconds is None:
        no_execution_grace_seconds = downstream_int(
            "n8n_no_execution_grace_seconds", minimum=1
        )
    if callback_grace_seconds is None:
        callback_grace_seconds = downstream_int(
            "n8n_callback_grace_seconds", minimum=1
        )
    current = now or _utc_now()
    report: dict[str, Any] = {
        "success": True,
        "locked": False,
        "checked": 0,
        "execution_ids_saved": [],
        "terminalized": [],
    }
    if not hunter_path.exists() or not n8n_path.exists():
        report.update({"success": False, "reason": "database_missing"})
        return report
    lock_path = hunter_path.parent / "diagnostics" / "private" / f"{PATCH_ID}.lock"
    with _process_lock(lock_path) as acquired:
        if not acquired:
            report["locked"] = True
            return report
        hunter = _connect(hunter_path)
        n8n = _connect(n8n_path, readonly=True)
        try:
            if "n8n_dispatch_queue" not in _table_names(hunter):
                report.update({"success": False, "reason": "queue_table_missing"})
                return report
            queue_cols = _columns(hunter, "n8n_dispatch_queue")
            status_col = "queue_status" if "queue_status" in queue_cols else "status" if "status" in queue_cols else None
            if not status_col:
                report.update({"success": False, "reason": "queue_status_column_missing"})
                return report
            placeholders = ",".join("?" for _ in OPEN_QUEUE_STATUSES)
            rows = hunter.execute(
                f'SELECT * FROM n8n_dispatch_queue WHERE lower(COALESCE("{status_col}",\'\')) '
                f'IN ({placeholders}) ORDER BY id',
                sorted(OPEN_QUEUE_STATUSES),
            ).fetchall()
            for row in rows:
                queue = dict(row)
                report["checked"] += 1
                queue_id = int(queue.get("id"))
                job_id = int(queue.get("job_id"))
                accepted = _parse_time(
                    queue.get("accepted_at") or queue.get("reserved_at") or queue.get("queued_at")
                )
                age_seconds = (current - accepted).total_seconds() if accepted else 0
                execution_id = _progress_execution_id(hunter, queue_id)
                execution = _execution_by_id(n8n, execution_id) if execution_id else None
                if execution is None:
                    execution = find_webhook_execution_for_queue(
                        queue,
                        hunter_db=hunter_path,
                        n8n_db=n8n_path,
                    )
                    if execution:
                        execution_id = int(execution["id"])
                        _save_execution_id(hunter, queue_id, execution_id)
                        report["execution_ids_saved"].append(
                            {"queue_id": queue_id, "execution_id": execution_id}
                        )
                if execution:
                    status = str(execution.get("status") or "").casefold()
                    stopped = _parse_time(execution.get("stoppedAt"))
                    terminal_age = (
                        (current - stopped).total_seconds() if stopped else age_seconds
                    )
                    if status in TERMINAL_FAILURE_STATUSES:
                        code = f"n8n_webhook_execution_{status}"
                        if _terminalize_queue(
                            hunter,
                            queue_id=queue_id,
                            job_id=job_id,
                            execution_id=execution_id,
                            error_code=code,
                        ):
                            report["terminalized"].append(
                                {"queue_id": queue_id, "execution_id": execution_id, "error_code": code}
                            )
                    elif status in TERMINAL_SUCCESS_STATUSES:
                        if not _result_exists(hunter, job_id, queue_id) and terminal_age >= callback_grace_seconds:
                            code = "n8n_success_callback_missing"
                            if _terminalize_queue(
                                hunter,
                                queue_id=queue_id,
                                job_id=job_id,
                                execution_id=execution_id,
                                error_code=code,
                            ):
                                report["terminalized"].append(
                                    {"queue_id": queue_id, "execution_id": execution_id, "error_code": code}
                                )
                elif age_seconds >= no_execution_grace_seconds:
                    code = "n8n_webhook_execution_not_found"
                    if _terminalize_queue(
                        hunter,
                        queue_id=queue_id,
                        job_id=job_id,
                        execution_id=None,
                        error_code=code,
                    ):
                        report["terminalized"].append(
                            {"queue_id": queue_id, "execution_id": None, "error_code": code}
                        )
            hunter.commit()
            return report
        except Exception:
            hunter.rollback()
            raise
        finally:
            hunter.close()
            n8n.close()


def persist_jobspy_run_start(
    source_key: str | None,
    *,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> dict[str, Any]:
    """Persist dashboard provenance before any JobSpy provider call.

    This does not mark a source healthy or failed. It ensures that even an
    early provider exception retains the exact dashboard rules hash and
    configuration source that governed the attempted run.
    """
    clean_key = str(source_key or "").strip()
    if not clean_key:
        return {"persisted": False, "reason": "missing_source_key"}
    try:
        rules_hash = canonical_rules_hash(hunter_db)
        display = clean_key
        try:
            from app.jobspy_board_common import source_config  # type: ignore

            cfg = source_config(clean_key)
            display = str(cfg.get("display_name") or display)
        except Exception:
            pass

        connection = _connect(Path(hunter_db))
        try:
            if "source_health" not in _table_names(connection):
                return {"persisted": False, "reason": "source_health_missing"}
            cols = _columns(connection, "source_health")
            if "source_name" not in cols:
                return {"persisted": False, "reason": "source_name_missing"}
            provenance = {
                "configuration_source": "SQLite dashboard",
                "dashboard_targeting_gate": True,
                "personal_rules_hardcoded": False,
                "targeting_rules_hash": rules_hash,
                "run_stage": "provider_start",
                "source_key": clean_key,
                "started_at": utc_now_text(),
            }
            assignments: list[str] = []
            values: list[Any] = []
            for name, value in (
                ("targeting_rules_hash", rules_hash),
                ("filter_summary_json", json.dumps(provenance, ensure_ascii=False, sort_keys=True)),
            ):
                if name in cols:
                    assignments.append(f'"{name}" = ?')
                    values.append(value)
            if "updated_at" in cols:
                assignments.append('"updated_at" = CURRENT_TIMESTAMP')
            if not assignments:
                return {"persisted": False, "reason": "provenance_columns_missing"}
            values.append(display)
            cursor = connection.execute(
                f'UPDATE source_health SET {", ".join(assignments)} WHERE source_name = ?',
                values,
            )
            connection.commit()
            return {
                "persisted": cursor.rowcount > 0,
                "source_name": display,
                "targeting_rules_hash": rules_hash,
            }
        finally:
            connection.close()
    except Exception as error:
        return {
            "persisted": False,
            "reason": "source_health_update_failed",
            "error_type": type(error).__name__,
        }


def normalize_jobspy_result(
    result: Any,
    *,
    source_key: str | None = None,
    hunter_db: Path | str = DEFAULT_HUNTER_DB,
) -> Any:
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    summary = normalized.get("summary") if isinstance(normalized.get("summary"), dict) else {}
    errors = normalized.get("errors")
    if not isinstance(errors, list):
        errors = summary.get("errors") if isinstance(summary.get("errors"), list) else []
    raw = int(normalized.get("raw_jobs_found") or summary.get("raw_jobs_found") or 0)
    inserted = int(normalized.get("jobs_inserted") or normalized.get("inserted_jobs") or summary.get("jobs_inserted") or 0)
    eligible = int(normalized.get("jobs_after_dashboard_filters") or summary.get("jobs_after_dashboard_filters") or 0)
    successful_plans = normalized.get("successful_plans") or summary.get("successful_plans") or []
    has_success = bool(raw or inserted or eligible or successful_plans)
    if errors:
        if has_success:
            normalized["success"] = True
            normalized["status"] = "degraded"
            normalized["partial_success"] = True
        else:
            normalized["success"] = False
            normalized["status"] = "failed"
            normalized["partial_success"] = False
    else:
        normalized.setdefault("partial_success", False)
    normalized["configuration_source"] = "SQLite dashboard"
    normalized["dashboard_targeting_gate"] = True
    normalized["personal_rules_hardcoded"] = False
    normalized["targeting_rules_hash"] = canonical_rules_hash(hunter_db)

    # Persist auditable dashboard provenance for every JobSpy run. Provider-plan
    # errors become degraded/failed instead of being masked as healthy.
    if source_key:
        try:
            connection = _connect(Path(hunter_db))
            try:
                if "source_health" in _table_names(connection):
                    cols = _columns(connection, "source_health")
                    display = source_key
                    try:
                        from app.jobspy_board_common import source_config  # type: ignore
                        cfg = source_config(source_key)
                        display = str(cfg.get("display_name") or display)
                    except Exception:
                        pass
                    assignments = []
                    values: list[Any] = []
                    resolved_health = str(
                        normalized.get("status")
                        or ("healthy" if normalized.get("success") else "failed")
                    )
                    for name, value in (
                        ("health_status", resolved_health),
                        (
                            "last_error",
                            json.dumps(errors, ensure_ascii=False)[:4000]
                            if errors
                            else None,
                        ),
                        ("filter_summary_json", json.dumps(normalized, ensure_ascii=False, default=str)),
                        ("targeting_rules_hash", normalized["targeting_rules_hash"]),
                    ):
                        if name in cols:
                            assignments.append(f'"{name}" = ?')
                            values.append(value)
                    if "updated_at" in cols:
                        assignments.append('"updated_at" = CURRENT_TIMESTAMP')
                    if assignments and "source_name" in cols:
                        values.append(display)
                        connection.execute(
                            f'UPDATE source_health SET {", ".join(assignments)} WHERE source_name = ?',
                            values,
                        )

                state_table = "google_indeed_jobspy_state"
                if state_table in _table_names(connection):
                    state_cols = _columns(connection, state_table)
                    if "source_key" in state_cols:
                        state_assignments: list[str] = []
                        state_values: list[Any] = []
                        for name, value in (
                            ("last_status", str(normalized.get("status") or "unknown")),
                            ("last_result_json", json.dumps(normalized, ensure_ascii=False, default=str)),
                        ):
                            if name in state_cols:
                                state_assignments.append(f'"{name}" = ?')
                                state_values.append(value)
                        if state_assignments:
                            state_values.append(source_key)
                            connection.execute(
                                f'UPDATE "{state_table}" SET {", ".join(state_assignments)} WHERE source_key = ?',
                                state_values,
                            )
                connection.commit()
            finally:
                connection.close()
        except Exception:
            # Result normalization must not destroy an otherwise usable source run.
            normalized["health_persistence_warning"] = "source_health_update_failed"
    return normalized


def self_test() -> dict[str, Any]:
    results: dict[str, Any] = {}
    sample_us = {
        "title": "Human Resources Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "remote_type": "On-site",
    }
    sample_remote = {
        "title": "People Analytics Analyst",
        "location_raw": "Remote - United States",
        "country": "US",
        "remote_type": "Remote",
    }
    sample_foreign = {
        "title": "HR Operations Analyst",
        "location_raw": "Portishead, England, United Kingdom",
        "country": "US",
        "remote_type": "Hybrid",
    }
    results["us_evidence"] = bool(us_location_evidence(sample_us))
    results["remote_us_evidence"] = bool(us_location_evidence(sample_remote))
    results["foreign_conflict"] = bool(explicit_foreign_evidence(sample_foreign))
    results["arrangement_onsite"] = arrangement_for_job(sample_us) == "onsite"
    results["arrangement_remote"] = arrangement_for_job(sample_remote) == "remote"
    results["hard_reject_match"] = bool(
        matched_hard_rejects(
            {"description_raw": "This position requires 3+ years of experience."},
            ["3+ years"],
        )
    )
    if not all(results.values()):
        raise AssertionError(results)
    return results


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2, sort_keys=True))
