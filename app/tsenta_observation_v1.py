"""Privacy-minimized Tsenta observation and Native prediction deltas.

Only data returned by authorized user-facing MCP tools is accepted.  Submitted
answer values, candidate identifiers, employer URLs, raw resumes and browser
internals are intentionally excluded.  ``OBSERVED``, ``INFERRED`` and
``UNAVAILABLE`` are explicit evidence grades rather than interchangeable labels.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app import application_question_catalog_v1 as question_catalog
from app.database import get_connection
from app.tenant_foundation import current_owner
from app.tenant_foundation import ensure_schema as ensure_tenant_schema

OBSERVATION_VERSION = "munshi-tsenta-observation-v1"
EVIDENCE_GRADES = frozenset({"OBSERVED", "INFERRED", "UNAVAILABLE"})
_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ATS = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_STATUS_TOKEN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,79}$")
_SECRETISH = re.compile(
    r"(?i)(bearer\s+[a-z0-9._-]+|api[_ -]?key|oauth|password|cookie|session[_ -]?token)"
)
_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"\b\d{5,}\b")

SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS tsenta_observations_v1(
        observation_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        provider_application_ref_sha256 TEXT NOT NULL,
        ats_type TEXT NOT NULL,
        observation_kind TEXT NOT NULL,
        evidence_grade TEXT NOT NULL CHECK(evidence_grade IN (
            'OBSERVED','INFERRED','UNAVAILABLE'
        )),
        field_fingerprint TEXT,
        label_template TEXT,
        semantic_question_key TEXT,
        field_kind TEXT,
        required INTEGER,
        editable INTEGER,
        has_value INTEGER,
        option_count INTEGER,
        provider_status TEXT,
        error_code TEXT,
        observed_json TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        UNIQUE(tenant_id,user_id,provider_application_ref_sha256,observation_id),
        FOREIGN KEY(tenant_id,user_id)
          REFERENCES tenant_memberships(tenant_id,user_id) ON DELETE RESTRICT
    );""",
    """CREATE INDEX IF NOT EXISTS idx_tsenta_observations_v1_ats_kind
       ON tsenta_observations_v1(tenant_id,user_id,ats_type,observation_kind,observed_at);""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"))


def _sanitize_label(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    text = _EMAIL.sub("<email>", text)
    text = _URL.sub("<url>", text)
    text = _LONG_NUMBER.sub("<number>", text)
    if _SECRETISH.search(text):
        raise ValueError("Tsenta observation contains credential-like text.")
    return text


def _ats_slug(value: Any) -> str:
    """Accept unknown ATS families; reject values containing private content."""
    result = re.sub(r"[\s/]+", "-", str(value or "unknown").strip().casefold())
    if not _ATS.fullmatch(result):
        raise ValueError("ATS type must be a bounded platform identifier.")
    return result


def _status_token(value: Any) -> str:
    result = str(value or "UNKNOWN").strip().upper()
    if not _STATUS_TOKEN.fullmatch(result):
        raise ValueError("Provider status must be a bounded machine token.")
    return result


def _application_ref(value: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError("Provider application reference is required in memory.")
    return hashlib.sha256(resolved.encode()).hexdigest()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _field_fingerprint(label: str, kind: str, option_labels: Sequence[str]) -> str:
    payload = {
        "label": label.casefold(),
        "kind": kind,
        "options": sorted(item.casefold() for item in option_labels),
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _semantic_key(label: str) -> str:
    try:
        requirement = question_catalog.requirement_from_question(label, required=True)
    except (TypeError, ValueError):
        return "unknown"
    return str(requirement.get("question_key") or "unknown")


def observations_from_review(
    *,
    provider_application_id: str,
    ats_type: str,
    review: Mapping[str, Any],
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Create value-free field observations from get_application_review."""
    if not isinstance(review, Mapping):
        raise TypeError("Tsenta review must be an object.")
    reference = _application_ref(provider_application_id)
    ats = _ats_slug(ats_type)
    timestamp = str(observed_at or _now())
    if review.get("awaitingReview") is not True:
        return [
            {
                "version": OBSERVATION_VERSION,
                "provider_application_ref_sha256": reference,
                "ats_type": ats,
                "observation_kind": "APPLICATION_REVIEW",
                "evidence_grade": "UNAVAILABLE",
                "unavailable_reason": "application_not_paused_for_review",
                "observed_at": timestamp,
                "answer_value_recorded": False,
            }
        ]

    fields = review.get("fields") or []
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
        raise TypeError("Tsenta review fields must be a list.")
    if len(fields) > 500:
        raise ValueError("Tsenta review exceeds the bounded observation budget.")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(fields):
        if not isinstance(raw, Mapping):
            continue
        label = _sanitize_label(raw.get("label"))
        kind = str(raw.get("kind") or "unknown").strip().casefold()
        if not _KIND.fullmatch(kind):
            kind = "unknown"
        raw_options = raw.get("options") or []
        option_labels: list[str] = []
        if isinstance(raw_options, Sequence) and not isinstance(
            raw_options, (str, bytes, bytearray)
        ):
            for option in list(raw_options)[:100]:
                if isinstance(option, Mapping):
                    option_labels.append(_sanitize_label(option.get("label")))
        fingerprint = _field_fingerprint(label, kind, option_labels)
        observation = {
            "version": OBSERVATION_VERSION,
            "provider_application_ref_sha256": reference,
            "ats_type": ats,
            "observation_kind": "FORM_FIELD",
            "evidence_grade": "OBSERVED",
            "field_index": int(index),
            "field_fingerprint": fingerprint,
            "label_template": label,
            "semantic_question_key": _semantic_key(label),
            "field_kind": kind,
            "required": raw.get("required") is True,
            "editable": raw.get("editable") is True,
            "has_value": (
                raw.get("hasValue") is True
                if isinstance(raw.get("hasValue"), bool)
                else _has_value(raw.get("value"))
            ),
            "option_count": len(option_labels),
            "option_label_fingerprints": [
                hashlib.sha256(item.casefold().encode()).hexdigest()
                for item in option_labels
            ],
            "observed_at": timestamp,
            "answer_value_recorded": False,
            "option_wire_values_recorded": False,
        }
        result.append(observation)
    return result


def observation_from_status(
    *,
    provider_application_id: str,
    ats_type: str,
    status: Mapping[str, Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    if not isinstance(status, Mapping):
        raise TypeError("Tsenta status must be an object.")
    message = status.get("statusMessage")
    error_title = None
    if isinstance(message, Mapping):
        error_title = _sanitize_label(message.get("title")) or None
    return {
        "version": OBSERVATION_VERSION,
        "provider_application_ref_sha256": _application_ref(provider_application_id),
        "ats_type": _ats_slug(ats_type),
        "observation_kind": "APPLICATION_STATUS",
        "evidence_grade": "OBSERVED",
        "provider_status": _status_token(status.get("status")),
        # Provider error codes may unexpectedly contain applicant text.
        "error_code": (
            _status_token(status["errorCode"])
            if isinstance(status.get("errorCode"), str)
            and _STATUS_TOKEN.fullmatch(status["errorCode"].strip().upper())
            else None
        ),
        "error_title": error_title,
        "needs_action": status.get("needsAction") is True,
        "is_terminal": status.get("isTerminal") is True,
        "is_refunded": status.get("isRefunded") is True,
        "resume_ready": dict(status.get("resume") or {}).get("ready") is True,
        "employer_receipt_observed": False,
        "observed_at": str(observed_at or _now()),
        "answer_value_recorded": False,
    }


def compare_native_predictions(
    observed_fields: Sequence[Mapping[str, Any]],
    native_predictions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare Native predictions to confirmed review fields without guessing."""
    predicted_by_fingerprint = {
        str(row.get("field_fingerprint") or ""): dict(row)
        for row in native_predictions
        if str(row.get("field_fingerprint") or "")
    }
    predicted_by_semantic = {
        (
            str(row.get("semantic_question_key") or "unknown"),
            str(row.get("field_kind") or "unknown"),
        ): dict(row)
        for row in native_predictions
    }
    deltas: list[dict[str, Any]] = []
    matched_prediction_ids: set[str] = set()
    for observed in observed_fields:
        if (
            str(observed.get("evidence_grade")) != "OBSERVED"
            or str(observed.get("observation_kind")) != "FORM_FIELD"
        ):
            continue
        fingerprint = str(observed.get("field_fingerprint") or "")
        semantic = str(observed.get("semantic_question_key") or "unknown")
        kind = str(observed.get("field_kind") or "unknown")
        prediction = predicted_by_fingerprint.get(fingerprint)
        match_basis = "field_fingerprint"
        if prediction is None:
            prediction = predicted_by_semantic.get((semantic, kind))
            match_basis = "semantic_and_kind"
        prediction_id = str((prediction or {}).get("prediction_id") or "")
        if prediction_id:
            matched_prediction_ids.add(prediction_id)
        differences: list[str] = []
        if prediction is None:
            differences.append("native_field_missing")
        else:
            for key in ("required", "editable", "has_value", "option_count"):
                if key in prediction and prediction.get(key) != observed.get(key):
                    differences.append(f"{key}_mismatch")
        deltas.append(
            {
                "field_fingerprint": fingerprint,
                "semantic_question_key": semantic,
                "field_kind": kind,
                "evidence_grade": "OBSERVED",
                "native_prediction_id": prediction_id or None,
                "match_basis": match_basis if prediction is not None else None,
                "differences": differences,
                "prediction_correct": not differences,
            }
        )
    for prediction in native_predictions:
        prediction_id = str(prediction.get("prediction_id") or "")
        if prediction_id and prediction_id not in matched_prediction_ids:
            deltas.append(
                {
                    "field_fingerprint": str(prediction.get("field_fingerprint") or "")
                    or None,
                    "semantic_question_key": str(
                        prediction.get("semantic_question_key") or "unknown"
                    ),
                    "field_kind": str(prediction.get("field_kind") or "unknown"),
                    "evidence_grade": "INFERRED",
                    "native_prediction_id": prediction_id,
                    "match_basis": None,
                    "differences": ["native_prediction_not_observed"],
                    "prediction_correct": False,
                }
            )
    return {
        "version": OBSERVATION_VERSION,
        "observed_field_count": sum(
            row.get("evidence_grade") == "OBSERVED"
            and row.get("observation_kind") == "FORM_FIELD"
            for row in observed_fields
        ),
        "native_prediction_count": len(native_predictions),
        "deltas": deltas,
        "answer_values_compared": False,
        "browser_internals_available": False,
    }


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


def persist_observations(observations: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Persist only the allowlisted, value-free observation projection."""
    connection = get_connection()
    inserted = replayed = 0
    try:
        ensure_schema(connection)
        owner = current_owner(connection)
        for raw in observations:
            observation = dict(raw)
            grade = str(observation.get("evidence_grade") or "")
            if grade not in EVIDENCE_GRADES:
                raise ValueError("Invalid Tsenta observation evidence grade.")
            if observation.get("answer_value_recorded") is not False:
                raise ValueError(
                    "Tsenta observation attempted to persist an answer value."
                )
            safe = {
                key: observation.get(key)
                for key in (
                    "version",
                    "observation_kind",
                    "evidence_grade",
                    "field_fingerprint",
                    "semantic_question_key",
                    "field_kind",
                    "required",
                    "editable",
                    "has_value",
                    "option_count",
                    "provider_status",
                    "error_code",
                    "unavailable_reason",
                    "needs_action",
                    "is_terminal",
                    "is_refunded",
                    "resume_ready",
                    "employer_receipt_observed",
                    "answer_value_recorded",
                )
                if key in observation
            }
            ref = str(observation.get("provider_application_ref_sha256") or "")
            if not re.fullmatch(r"[0-9a-f]{64}", ref):
                raise ValueError("Provider application reference digest is invalid.")
            ats = _ats_slug(observation.get("ats_type"))
            observed_at = str(observation.get("observed_at") or _now())
            identity = {
                "ref": ref,
                "ats": ats,
                "kind": str(observation.get("observation_kind") or "UNKNOWN"),
                "field": observation.get("field_fingerprint"),
                "status": observation.get("provider_status"),
                "error": observation.get("error_code"),
            }
            observation_id = (
                "tsenta-observation-"
                + hashlib.sha256(_canonical(identity).encode()).hexdigest()[:32]
            )
            cursor = connection.execute(
                """INSERT OR IGNORE INTO tsenta_observations_v1(
                     observation_id,tenant_id,user_id,
                     provider_application_ref_sha256,ats_type,observation_kind,
                     evidence_grade,field_fingerprint,label_template,
                     semantic_question_key,field_kind,required,editable,has_value,
                     option_count,provider_status,error_code,observed_json,observed_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    observation_id,
                    owner.tenant_id,
                    owner.user_id,
                    ref,
                    ats,
                    str(observation.get("observation_kind") or "UNKNOWN")[:80],
                    grade,
                    observation.get("field_fingerprint"),
                    None,  # No provider-supplied question text in durable storage.
                    observation.get("semantic_question_key"),
                    observation.get("field_kind"),
                    int(bool(observation.get("required")))
                    if "required" in observation
                    else None,
                    int(bool(observation.get("editable")))
                    if "editable" in observation
                    else None,
                    int(bool(observation.get("has_value")))
                    if "has_value" in observation
                    else None,
                    int(observation.get("option_count") or 0)
                    if "option_count" in observation
                    else None,
                    observation.get("provider_status"),
                    observation.get("error_code"),
                    _canonical(safe),
                    observed_at,
                ),
            )
            if cursor.rowcount:
                inserted += 1
            else:
                # Status can legitimately return to an earlier machine state.
                # Update its latest sanitized observation time so the learning
                # cohort never treats the intermediate status as permanent.
                # Unique application counts still cannot increase on replay.
                if identity["kind"] == "APPLICATION_STATUS":
                    connection.execute(
                        """UPDATE tsenta_observations_v1
                              SET observed_at=?,observed_json=?,evidence_grade=?
                            WHERE observation_id=? AND tenant_id=? AND user_id=?
                              AND observed_at < ?""",
                        (
                            observed_at, _canonical(safe), grade,
                            observation_id, owner.tenant_id, owner.user_id,
                            observed_at,
                        ),
                    )
                replayed += 1
        connection.commit()
        return {"inserted": inserted, "replayed": replayed}
    finally:
        connection.close()
