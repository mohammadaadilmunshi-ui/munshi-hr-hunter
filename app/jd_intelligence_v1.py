"""Stage B — canonical JD Intelligence V1.

This module turns the existing Hunter-owned job snapshot into a deterministic,
content-addressed employer-requirements snapshot. It does not use Candidate Truth
and has no resume, browser, ATS, Gmail, n8n, outreach, or submission authority.

The parser is deliberately conservative: it classifies only text that exists in
canonical job fields, preserves exact source text/field provenance, keeps
ambiguous constraints explicit, and fails closed when the underlying job changes
before persistence.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.database import get_connection
from app.phase67_common import canonical_text, safe_owned_job_snapshot, sha256_json
from app.tenant_foundation import current_owner, ensure_schema as ensure_tenant_schema

JD_INTELLIGENCE_VERSION = "stage-b-jd-intelligence-v1"
JD_INTELLIGENCE_AUTHORITY = "munshi-hr-hunter"
SUBMISSION_AUTHORITY = False

REQUIREMENT_TYPES = frozenset(
    {
        "RESPONSIBILITY",
        "SKILL",
        "TOOL",
        "PROCESS",
        "DOMAIN_KNOWLEDGE",
        "EXPERIENCE",
        "EDUCATION",
        "CERTIFICATION",
        "LANGUAGE",
        "LOCATION",
        "WORKPLACE",
        "EMPLOYMENT_TYPE",
        "COMPENSATION",
        "WORK_AUTHORIZATION",
        "SPONSORSHIP",
        "CITIZENSHIP",
        "CLEARANCE",
        "TRAVEL",
        "SHIFT",
        "LICENSE",
        "OTHER_ELIGIBILITY",
        "OTHER",
    }
)
REQUIREMENT_PRIORITIES = frozenset(
    {"MUST_HAVE", "PREFERRED", "CORE_RESPONSIBILITY", "BONUS", "CONTEXT", "UNKNOWN"}
)

_SOURCE_FIELDS = (
    "title",
    "description_raw",
    "responsibilities",
    "qualifications",
    "preferred_qualifications",
    "preferred_skills",
    "skills_keywords",
    "work_authorization",
    "location_raw",
    "remote_type",
    "employment_type",
    "salary_raw",
)

_FIELD_DEFAULT_PRIORITY = {
    "responsibilities": "CORE_RESPONSIBILITY",
    "qualifications": "MUST_HAVE",
    "preferred_qualifications": "PREFERRED",
    "preferred_skills": "PREFERRED",
    "skills_keywords": "MUST_HAVE",
    "work_authorization": "MUST_HAVE",
    "location_raw": "CONTEXT",
    "remote_type": "CONTEXT",
    "employment_type": "CONTEXT",
    "salary_raw": "CONTEXT",
    "title": "CONTEXT",
    "description_raw": "UNKNOWN",
}

_SPLIT_RE = re.compile(r"(?:\r?\n+|(?<=[.!?])\s+(?=[A-Z0-9•*\-]))")
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s*")
_SPACE_RE = re.compile(r"\s+")
_YEARS_RE = re.compile(r"\b(?P<minimum>\d{1,2})(?:\s*[-–]\s*(?P<maximum>\d{1,2}))?\s*\+?\s+years?\b", re.I)
_MONEY_RE = re.compile(
    r"(?P<currency>[$€£])\s*(?P<minimum>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?:-|–|to)\s*(?:[$€£]\s*)?(?P<maximum>\d[\d,]*(?:\.\d+)?))?",
    re.I,
)

_TYPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("SPONSORSHIP", re.compile(r"\bsponsor(?:ship|ed|ing)?\b|\bvisa sponsorship\b", re.I)),
    (
        "WORK_AUTHORIZATION",
        re.compile(
            r"\bauthori[sz](?:ed|ation)\s+to\s+work\b|\bwork authorization\b|\bemployment authorization\b",
            re.I,
        ),
    ),
    ("CITIZENSHIP", re.compile(r"\bU\.?S\.?\s+citizen(?:ship)?\b|\bcitizenship\b", re.I)),
    ("CLEARANCE", re.compile(r"\bsecurity clearance\b|\bsecret clearance\b|\btop secret\b|\bTS/SCI\b", re.I)),
    ("COMPENSATION", re.compile(r"\b(?:salary|pay range|compensation|hourly rate|base pay)\b|[$€£]\s*\d", re.I)),
    ("EDUCATION", re.compile(r"\b(?:bachelor'?s|master'?s|doctorate|ph\.?d\.?|degree|GED|high school diploma)\b", re.I)),
    ("EXPERIENCE", re.compile(r"\b\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s*\+?\s+years?\b|\byears? of experience\b", re.I)),
    ("CERTIFICATION", re.compile(r"\b(?:certification|certified|certificate|SHRM-CP|SHRM-SCP|PHR|SPHR|PMP)\b", re.I)),
    ("LANGUAGE", re.compile(r"\b(?:bilingual|fluent|proficient)\b.*\b(?:English|Spanish|French|German|Mandarin|Arabic)\b", re.I)),
    ("TRAVEL", re.compile(r"\btravel\b|\b\d{1,3}%\s+travel\b", re.I)),
    ("SHIFT", re.compile(r"\b(?:night|evening|weekend|rotating|shift)\b", re.I)),
    ("LICENSE", re.compile(r"\bdriver'?s license\b|\blicen[cs]e required\b", re.I)),
    ("WORKPLACE", re.compile(r"\b(?:remote|hybrid|on[- ]?site|in[- ]?office)\b", re.I)),
    ("EMPLOYMENT_TYPE", re.compile(r"\b(?:full[- ]?time|part[- ]?time|contract|temporary|internship)\b", re.I)),
    ("LOCATION", re.compile(r"\b(?:relocat(?:e|ion)|located in|based in|reside in|commut(?:e|ing))\b", re.I)),
)

_PRIORITY_PATTERNS = (
    ("PREFERRED", re.compile(r"\b(?:preferred|nice to have|a plus|bonus|ideally)\b", re.I)),
    ("MUST_HAVE", re.compile(r"\b(?:must|required|minimum qualification|need to have|shall)\b", re.I)),
)

_TOOL_HINTS = frozenset(
    {
        "workday",
        "greenhouse",
        "lever",
        "ashby",
        "successfactors",
        "sap",
        "adp",
        "ukg",
        "excel",
        "power bi",
        "tableau",
        "sql",
        "python",
        "r",
        "salesforce",
        "jira",
        "slack",
    }
)

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS jd_intelligence_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        job_id INTEGER NOT NULL,
        job_snapshot_sha256 TEXT NOT NULL CHECK(length(job_snapshot_sha256)=64),
        source_text_sha256 TEXT NOT NULL CHECK(length(source_text_sha256)=64),
        parser_version TEXT NOT NULL,
        snapshot_digest TEXT NOT NULL CHECK(length(snapshot_digest)=64),
        snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT,
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT,
        UNIQUE(tenant_id,user_id,job_id,job_snapshot_sha256,parser_version),
        UNIQUE(tenant_id,user_id,snapshot_digest)
    );""",
    """CREATE INDEX IF NOT EXISTS idx_jd_intelligence_owner_job
       ON jd_intelligence_snapshots(tenant_id,user_id,job_id,created_at DESC);""",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection | None = None) -> None:
    owns = connection is None
    connection = connection or get_connection()
    try:
        ensure_tenant_schema(connection)
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if owns:
            connection.commit()
    finally:
        if owns:
            connection.close()


def _normalize_fragment(value: Any) -> str:
    text = _BULLET_PREFIX_RE.sub("", str(value or "")).strip()
    return _SPACE_RE.sub(" ", text)


def _fragments(field: str, value: Any) -> list[dict[str, Any]]:
    raw = str(value or "")
    if not raw.strip():
        return []
    if field == "skills_keywords":
        candidates = re.split(r"[,;|\n]+", raw)
    else:
        candidates = _SPLIT_RE.split(raw)
    result: list[dict[str, Any]] = []
    cursor = 0
    for candidate in candidates:
        text = _normalize_fragment(candidate)
        if len(text) < 3:
            continue
        start = raw.find(candidate, cursor)
        if start < 0:
            start = raw.find(candidate)
        if start < 0:
            start = 0
        end = start + len(candidate)
        cursor = max(cursor, end)
        result.append(
            {
                "source_field": field,
                "source_start": start,
                "source_end": end,
                "exact_text": text,
            }
        )
    return result


def _priority(field: str, text: str) -> str:
    for label, pattern in _PRIORITY_PATTERNS:
        if pattern.search(text):
            return label
    return _FIELD_DEFAULT_PRIORITY.get(field, "UNKNOWN")


def _looks_like_tool(text: str) -> bool:
    folded = text.casefold()
    return any(
        hint == folded
        or hint in folded
        or re.search(rf"(?<![a-z0-9]){re.escape(hint)}(?![a-z0-9])", folded)
        for hint in _TOOL_HINTS
    )


def _requirement_type(field: str, text: str) -> tuple[str, float]:
    for label, pattern in _TYPE_RULES:
        if pattern.search(text):
            return label, 0.98
    if field == "responsibilities":
        return "RESPONSIBILITY", 1.0
    if field in {"preferred_skills", "skills_keywords"}:
        return ("TOOL", 0.92) if _looks_like_tool(text) else ("SKILL", 0.90)
    if field in {"qualifications", "preferred_qualifications"}:
        return ("TOOL", 0.90) if _looks_like_tool(text) else ("SKILL", 0.72)
    if field == "location_raw":
        return "LOCATION", 1.0
    if field == "remote_type":
        return "WORKPLACE", 1.0
    if field == "employment_type":
        return "EMPLOYMENT_TYPE", 1.0
    if field == "salary_raw":
        return "COMPENSATION", 1.0
    if field == "work_authorization":
        return "WORK_AUTHORIZATION", 1.0
    return "OTHER", 0.50


def _structured_constraints(requirement_type: str, text: str) -> dict[str, Any]:
    constraints: dict[str, Any] = {}
    years = _YEARS_RE.search(text)
    if years:
        constraints["experience_years"] = {
            "minimum": int(years.group("minimum")),
            "maximum": int(years.group("maximum")) if years.group("maximum") else None,
            "source_text": years.group(0),
        }
    money = _MONEY_RE.search(text)
    if money:
        symbol = money.group("currency")
        currency = {"$": "USD", "€": "EUR", "£": "GBP"}.get(symbol, "UNKNOWN")
        constraints["compensation"] = {
            "currency": currency,
            "minimum": float(money.group("minimum").replace(",", "")),
            "maximum": (
                float(money.group("maximum").replace(",", ""))
                if money.group("maximum")
                else None
            ),
            "cadence": _compensation_cadence(text),
            "source_text": money.group(0),
        }
    if requirement_type == "WORKPLACE":
        folded = text.casefold()
        modes = [
            mode
            for mode, pattern in (
                ("remote", r"\bremote\b"),
                ("hybrid", r"\bhybrid\b"),
                ("onsite", r"\bon[- ]?site\b|\bin[- ]?office\b"),
            )
            if re.search(pattern, folded, re.I)
        ]
        if modes:
            constraints["workplace_modes"] = modes
    return constraints


def _compensation_cadence(text: str) -> str | None:
    folded = text.casefold()
    if re.search(r"\b(?:per hour|hourly|/hr|an hour)\b", folded):
        return "HOURLY"
    if re.search(r"\b(?:per year|annual|annually|yearly|/yr)\b", folded):
        return "ANNUAL"
    if re.search(r"\b(?:per month|monthly)\b", folded):
        return "MONTHLY"
    if re.search(r"\b(?:per week|weekly)\b", folded):
        return "WEEKLY"
    return None


def _source_text_payload(job: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"field": field, "text": str(job.get(field) or "")}
        for field in _SOURCE_FIELDS
        if str(job.get(field) or "").strip()
    ]


def _dedupe_requirements(requirements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in requirements:
        key = (
            str(item["source_field"]),
            canonical_text(item["exact_text"]).casefold(),
            str(item["priority"]),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _keywords(requirements: list[dict[str, Any]], job: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    raw_skills = str(job.get("skills_keywords") or "")
    for item in re.split(r"[,;|\n]+", raw_skills):
        text = canonical_text(item)
        if text and text.casefold() not in {value.casefold() for value in values}:
            values.append(text)
    for requirement in requirements:
        if requirement["type"] not in {"SKILL", "TOOL", "DOMAIN_KNOWLEDGE"}:
            continue
        text = canonical_text(requirement["exact_text"])
        if 2 <= len(text.split()) <= 8 and text.casefold() not in {value.casefold() for value in values}:
            values.append(text)
        if len(values) >= 120:
            break
    return values


def build_snapshot(job_snapshot: Mapping[str, Any]) -> dict[str, Any]:
    job = job_snapshot.get("job")
    if not isinstance(job, Mapping):
        raise ValueError("JD Intelligence requires a canonical Hunter job snapshot.")
    job_digest = str(job_snapshot.get("job_snapshot_sha256") or "")
    if len(job_digest) != 64:
        raise ValueError("JD Intelligence requires a valid job snapshot digest.")

    requirements: list[dict[str, Any]] = []
    sequence = 0
    for field in _SOURCE_FIELDS:
        value = job.get(field)
        for fragment in _fragments(field, value):
            sequence += 1
            req_type, confidence = _requirement_type(field, fragment["exact_text"])
            priority = _priority(field, fragment["exact_text"])
            requirements.append(
                {
                    "requirement_id": f"JDREQ-{sequence:03d}",
                    "type": req_type,
                    "priority": priority,
                    "exact_text": fragment["exact_text"],
                    "normalized_text": canonical_text(fragment["exact_text"]).casefold(),
                    "source_field": field,
                    "source_start": int(fragment["source_start"]),
                    "source_end": int(fragment["source_end"]),
                    "classification_confidence": round(float(confidence), 4),
                    "structured_constraints": _structured_constraints(
                        req_type, fragment["exact_text"]
                    ),
                }
            )

    requirements = _dedupe_requirements(requirements)
    for index, requirement in enumerate(requirements, start=1):
        requirement["requirement_id"] = f"JDREQ-{index:03d}"

    source_payload = _source_text_payload(job)
    source_text_sha256 = hashlib.sha256(
        json.dumps(source_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    unknowns: list[str] = []
    if not requirements:
        unknowns.append("no_extractable_job_requirements")
    compensation_requirements = [
        item for item in requirements if item["type"] == "COMPENSATION"
    ]
    if compensation_requirements and any(
        (item.get("structured_constraints") or {}).get("compensation", {}).get("cadence") is None
        for item in compensation_requirements
    ):
        unknowns.append("compensation_cadence")

    payload = {
        "contract_version": JD_INTELLIGENCE_VERSION,
        "authority": JD_INTELLIGENCE_AUTHORITY,
        "tenant_id": str(job_snapshot["tenant_id"]),
        "user_id": str(job_snapshot["user_id"]),
        "job_id": int(job["id"]),
        "job_snapshot_sha256": job_digest,
        "source_text_sha256": source_text_sha256,
        "role": canonical_text(job.get("title")) or None,
        "company": canonical_text(job.get("company_name")) or None,
        "seniority": None,
        "requirements": requirements,
        "keywords": _keywords(requirements, job),
        "unknowns": sorted(set(unknowns)),
        "diagnostics": {
            "parser_mode": "deterministic_grounded_v1",
            "requirement_count": len(requirements),
            "source_fields_used": [item["field"] for item in source_payload],
            "llm_calls": 0,
            "automatic_actions_executed": False,
        },
        "mutation_authority": False,
        "submission_authority": False,
    }
    digest = sha256_json(payload)
    return {
        **payload,
        "snapshot_id": f"jd-intel-{digest[:24]}",
        "snapshot_digest": digest,
        "generated_at": _now(),
    }


def _snapshot_digest_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key not in {"snapshot_id", "snapshot_digest", "generated_at"}
    }


def validate_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(snapshot)
    if value.get("contract_version") != JD_INTELLIGENCE_VERSION:
        raise ValueError("Unsupported JD Intelligence version.")
    if value.get("submission_authority") is not False:
        raise ValueError("JD Intelligence cannot have submission authority.")
    requirements = value.get("requirements")
    if not isinstance(requirements, list):
        raise ValueError("JD Intelligence requirements must be a list.")
    seen: set[str] = set()
    for item in requirements:
        if not isinstance(item, Mapping):
            raise ValueError("JD Intelligence requirement must be an object.")
        requirement_id = str(item.get("requirement_id") or "")
        if requirement_id in seen or not re.fullmatch(r"JDREQ-\d{3,}", requirement_id):
            raise ValueError("JD Intelligence requirement ids must be unique and canonical.")
        seen.add(requirement_id)
        if item.get("type") not in REQUIREMENT_TYPES:
            raise ValueError("Unsupported JD requirement type.")
        if item.get("priority") not in REQUIREMENT_PRIORITIES:
            raise ValueError("Unsupported JD requirement priority.")
        source_field = str(item.get("source_field") or "")
        if source_field not in _SOURCE_FIELDS:
            raise ValueError("JD requirement source field is not canonical.")
        if not str(item.get("exact_text") or "").strip():
            raise ValueError("JD requirement exact source text is required.")
    digest = sha256_json(_snapshot_digest_payload(value))
    if value.get("snapshot_digest") and str(value["snapshot_digest"]) != digest:
        raise ValueError("JD Intelligence snapshot digest mismatch.")
    return value


def analyze_job(job_id: int, *, persist: bool = True) -> dict[str, Any]:
    before = safe_owned_job_snapshot(int(job_id))
    snapshot = build_snapshot(before)
    validate_snapshot(snapshot)
    if not persist:
        return snapshot

    after = safe_owned_job_snapshot(int(job_id))
    if after["job_snapshot_sha256"] != before["job_snapshot_sha256"]:
        raise RuntimeError(
            "The stored job changed during JD Intelligence analysis. Recompute from the current job snapshot."
        )

    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        if owner.tenant_id != snapshot["tenant_id"] or owner.user_id != snapshot["user_id"]:
            raise ValueError("JD Intelligence owner changed before persistence.")
        existing = connection.execute(
            """SELECT snapshot_json FROM jd_intelligence_snapshots
               WHERE tenant_id=? AND user_id=? AND job_id=? AND job_snapshot_sha256=? AND parser_version=?""",
            (
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                snapshot["job_snapshot_sha256"],
                JD_INTELLIGENCE_VERSION,
            ),
        ).fetchone()
        if existing is not None:
            return validate_snapshot(json.loads(existing["snapshot_json"]))

        connection.execute(
            """INSERT INTO jd_intelligence_snapshots(
                   snapshot_id,tenant_id,user_id,job_id,job_snapshot_sha256,
                   source_text_sha256,parser_version,snapshot_digest,snapshot_json
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                snapshot["snapshot_id"],
                owner.tenant_id,
                owner.user_id,
                int(job_id),
                snapshot["job_snapshot_sha256"],
                snapshot["source_text_sha256"],
                JD_INTELLIGENCE_VERSION,
                snapshot["snapshot_digest"],
                json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            ),
        )
        connection.commit()
        return snapshot
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def get_snapshot(snapshot_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT snapshot_json FROM jd_intelligence_snapshots
               WHERE snapshot_id=? AND tenant_id=? AND user_id=?""",
            (str(snapshot_id), owner.tenant_id, owner.user_id),
        ).fetchone()
        if row is None:
            raise LookupError("JD Intelligence snapshot is unavailable.")
        return validate_snapshot(json.loads(row["snapshot_json"]))
    finally:
        connection.close()


def latest_snapshot(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT snapshot_json FROM jd_intelligence_snapshots
               WHERE tenant_id=? AND user_id=? AND job_id=?
               ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
        if row is None:
            return None
        return validate_snapshot(json.loads(row["snapshot_json"]))
    finally:
        connection.close()


def snapshot_freshness(snapshot_id: str) -> dict[str, Any]:
    snapshot = get_snapshot(snapshot_id)
    current = safe_owned_job_snapshot(int(snapshot["job_id"]))
    fresh = current["job_snapshot_sha256"] == snapshot["job_snapshot_sha256"]
    return {
        "snapshot_id": snapshot_id,
        "job_id": int(snapshot["job_id"]),
        "fresh": fresh,
        "expected_job_snapshot_sha256": snapshot["job_snapshot_sha256"],
        "current_job_snapshot_sha256": current["job_snapshot_sha256"],
    }
