"""MUNSHI application-mail intelligence and one-time verification broker V2.

Safety boundaries:
- Cloudflare only classifies/normalizes and signs mail; it has no browser or submit authority.
- Hunter independently verifies the HMAC and independently classifies the RFC822 body.
- Passwords are never handled here.
- Verification codes/links are encrypted only while waiting for the exact Apply continuation.
  They are removed from durable storage on consume/expiry and are never written to
  the historical mail/event ledgers.
- Submission confirmation evidence is separate from submission authority and from
  ATS account verification state.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Header, HTTPException, Request, status

from app import application_production_submission_receipt_v1 as production_receipt
from app import inbound_mail as mail_v1
from app.ats_managed_identity import ensure_schema as ensure_identity_schema
from app.ats_verification_orchestrator import ensure_schema as ensure_verification_schema
from app.database import get_connection


PROTOCOL_VERSION = "v2"
ARTIFACT_ALGORITHM = "aes-gcm-mail-artifact-v1"
ARTIFACT_LEASE_SECONDS = 120
ARTIFACT_MAX_TTL_MINUTES = 30
SERVICE_TIMESTAMP_SKEW_SECONDS = 300
MAX_ARTIFACT_BYTES = 8192

ROUTER_CLASSES = {
    "ACCOUNT_VERIFICATION",
    "APPLICATION_CONFIRMATION",
    "COMMUNICATION",
    "SECURITY_INTERVENTION",
    "OTHER",
}
FINE_CLASSES = {
    "EMAIL_VERIFICATION_LINK",
    "EMAIL_VERIFICATION_CODE",
    "PASSWORD_RESET_LINK",
    "MAGIC_LOGIN_LINK",
    "APPLICATION_CONFIRMATION",
    "RECRUITER",
    "INTERVIEW",
    "ASSESSMENT",
    "REJECTION",
    "OFFER",
    "SECURITY_INTERVENTION",
    "OTHER",
}
COMMUNICATION_CLASSES = {"RECRUITER", "INTERVIEW", "ASSESSMENT", "REJECTION", "OFFER"}
ARTIFACT_CLASSES = {
    "EMAIL_VERIFICATION_LINK",
    "EMAIL_VERIFICATION_CODE",
    "PASSWORD_RESET_LINK",
    "MAGIC_LOGIN_LINK",
}

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])([0-9]{4,8})(?![A-Za-z0-9])")
_MESSAGE_SIGNATURE = re.compile(r"^v2=([0-9a-f]{64})$")
_SERVICE_SIGNATURE = re.compile(r"^v1=([0-9a-f]{64})$")
_SECURITY_PATTERNS = (
    r"\bcaptcha\b",
    r"\btext(?:ed)? (?:you|your|the)\b",
    r"\bsms\b",
    r"\bmobile (?:number|phone)\b",
    r"\bphone (?:number|verification|code)\b",
    r"\bauthenticator (?:app|code)\b",
    r"\btotp\b",
    r"\bpasskey\b",
    r"\bsecurity key\b",
    r"\bwebauthn\b",
    r"\bgovernment (?:id|identification)\b",
    r"\bidentity verification\b",
    r"\bselfie\b",
    r"\bbiometric\b",
    r"\bliveness\b",
    r"\bfraud (?:check|review|challenge)\b",
    r"\bsuspicious (?:activity|login)\b",
)
_CONFIRMATION_PATTERNS = (
    r"thank(?:s| you) for (?:your )?(?:application|applying)",
    r"application (?:was |has been )?(?:received|submitted)",
    r"we(?:'|’)ve received your application",
    r"your application (?:is|has been) complete",
)
_RESET_PATTERNS = (
    r"reset (?:your )?password",
    r"password reset",
    r"forgot (?:your )?password",
)
_MAGIC_PATTERNS = (
    r"magic (?:login )?link",
    r"sign in (?:with|using) (?:this|the) link",
    r"passwordless",
)
_VERIFY_PATTERNS = (
    r"verify (?:your )?(?:email|account)",
    r"activate (?:your )?(?:account|email)",
    r"email verification",
    r"verification code",
    r"confirm (?:your )?email",
)


class MailIntelligenceError(RuntimeError):
    """Non-sensitive mail intelligence failure."""


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value.strip())


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_time(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as error:
        raise MailIntelligenceError(f"{label} is invalid.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_router_class(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in ROUTER_CLASSES:
        raise PermissionError("Unsupported mail-router classification.")
    return normalized


def _signing_secret() -> bytes:
    value = str(os.getenv("MUNSHI_MAIL_SIGNING_SECRET") or "").encode("utf-8")
    if len(value) < 32:
        raise mail_v1.InboundMailConfigurationError(
            "Inbound mail signing secret must be at least 32 bytes."
        )
    return value


def canonical_signature_material_v2(
    *,
    timestamp_value: str,
    recipient: str,
    sender: str,
    message_id: str,
    router_class: str,
    raw_sha256: str,
) -> bytes:
    return "\n".join(
        (
            PROTOCOL_VERSION,
            timestamp_value,
            recipient,
            sender,
            message_id,
            router_class,
            raw_sha256,
        )
    ).encode("utf-8")


def verify_signature_v2(
    *,
    timestamp_value: str,
    recipient: str,
    sender: str,
    message_id: str,
    router_class: str,
    signature: str,
    raw: bytes,
    now: float | None = None,
) -> tuple[str, str]:
    if len(raw) > mail_v1.MAX_RAW_BYTES:
        raise OverflowError("Inbound message exceeds the raw size limit.")
    signed_at = mail_v1._signed_at(timestamp_value, now=now)
    normalized_class = _safe_router_class(router_class)
    match = _MESSAGE_SIGNATURE.fullmatch(str(signature or "").strip())
    if match is None:
        raise PermissionError("Invalid inbound mail signature.")
    digest = hashlib.sha256(raw).hexdigest()
    expected = hmac.new(
        _signing_secret(),
        canonical_signature_material_v2(
            timestamp_value=timestamp_value,
            recipient=recipient,
            sender=sender,
            message_id=message_id,
            router_class=normalized_class,
            raw_sha256=digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise PermissionError("Invalid inbound mail signature.")
    return signed_at, digest


def _combined_text(parsed: dict[str, Any]) -> str:
    return "\n".join(
        part for part in (
            str(parsed.get("subject") or ""),
            str(parsed.get("text_body") or ""),
            html.unescape(re.sub(r"<[^>]+>", " ", str(parsed.get("html_body") or ""))),
        ) if part
    )[:1_500_000]


def _matches_any(text: str, patterns: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in patterns)


def _extract_links(parsed: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for source in (str(parsed.get("text_body") or ""), str(parsed.get("html_body") or "")):
        candidates.extend(_URL_RE.findall(source))
    html_body = str(parsed.get("html_body") or "")
    if html_body:
        collector = _HrefCollector()
        try:
            collector.feed(html_body)
        except Exception:
            pass
        candidates.extend(collector.hrefs)
    result: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        value = html.unescape(raw).strip().rstrip(".,);]}")
        try:
            parsed_url = urlparse(value)
        except ValueError:
            continue
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _extract_codes(text: str) -> list[str]:
    if not _matches_any(text, _VERIFY_PATTERNS):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for match in _CODE_RE.finditer(text):
        code = match.group(1)
        # Reject likely years and common date fragments unless the nearby text says code.
        before = text[max(0, match.start() - 48):match.start()].lower()
        after = text[match.end():match.end() + 32].lower()
        context = before + " " + after
        if len(code) == 4 and code.startswith(("19", "20")) and "code" not in context:
            continue
        if code not in seen:
            seen.add(code)
            values.append(code)
    return values


def classify_message(parsed: dict[str, Any], *, router_class: str) -> dict[str, Any]:
    text = _combined_text(parsed)
    lowered = text.lower()
    router_class = _safe_router_class(router_class)
    security = _matches_any(text, _SECURITY_PATTERNS)
    if router_class == "SECURITY_INTERVENTION" or security:
        return {"classification": "SECURITY_INTERVENTION", "artifact": None}

    links = _extract_links(parsed)
    codes = _extract_codes(text)
    if _matches_any(text, _RESET_PATTERNS):
        return {"classification": "PASSWORD_RESET_LINK", "links": links, "codes": []}
    if _matches_any(text, _MAGIC_PATTERNS):
        return {"classification": "MAGIC_LOGIN_LINK", "links": links, "codes": []}
    if _matches_any(text, _VERIFY_PATTERNS):
        if len(codes) == 1:
            return {"classification": "EMAIL_VERIFICATION_CODE", "links": links, "codes": codes}
        return {"classification": "EMAIL_VERIFICATION_LINK", "links": links, "codes": codes}

    if _matches_any(text, _CONFIRMATION_PATTERNS):
        return {"classification": "APPLICATION_CONFIRMATION", "artifact": None}
    if re.search(r"\b(offer letter|job offer|offer of employment)\b", lowered):
        return {"classification": "OFFER", "artifact": None}
    if re.search(r"\b(unfortunately|not moving forward|other candidates|position has been filled)\b", lowered):
        return {"classification": "REJECTION", "artifact": None}
    if re.search(r"\b(interview|schedule a call|meet with|availability for a call)\b", lowered):
        return {"classification": "INTERVIEW", "artifact": None}
    if re.search(r"\b(assessment|coding test|skills test|take-home|questionnaire)\b", lowered):
        return {"classification": "ASSESSMENT", "artifact": None}
    if re.search(r"\b(recruiter|talent acquisition|sourcer|human resources)\b", lowered):
        return {"classification": "RECRUITER", "artifact": None}
    return {"classification": "OTHER", "artifact": None}


def _redacted_subject(subject: object, classification: str) -> str:
    if classification in ARTIFACT_CLASSES or classification == "SECURITY_INTERVENTION":
        return f"[{classification.lower().replace('_', ' ')}]"
    value = str(subject or "")[:1024]
    value = _URL_RE.sub("[link]", value)
    value = _CODE_RE.sub("[code]", value)
    return value


def _vault_key() -> bytes:
    try:
        key = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_VAULT_KEY") or "").encode())
    except Exception as error:
        raise MailIntelligenceError("One-time mail artifact vault is unavailable.") from error
    if len(key) != 32:
        raise MailIntelligenceError("One-time mail artifact vault is unavailable.")
    return key


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM
    except ImportError as error:
        raise MailIntelligenceError("One-time mail artifact vault is unavailable.") from error


def _artifact_aad(
    *,
    mail_event_id: str,
    request_id: str,
    tenant_id: str,
    user_id: str,
    kind: str,
    digest: str,
) -> bytes:
    return json.dumps(
        {
            "v": 2,
            "mail_event_id": mail_event_id,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "kind": kind,
            "digest": digest,
            "algorithm": ARTIFACT_ALGORITHM,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _encrypt_artifact(
    value: str,
    *,
    mail_event_id: str,
    request_id: str,
    tenant_id: str,
    user_id: str,
    kind: str,
) -> tuple[bytes, bytes, str]:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > MAX_ARTIFACT_BYTES:
        raise MailIntelligenceError("One-time verification artifact has an invalid size.")
    digest = hashlib.sha256(encoded).hexdigest()
    nonce = os.urandom(12)
    cipher = _aesgcm()(_vault_key()).encrypt(
        nonce,
        encoded,
        _artifact_aad(
            mail_event_id=mail_event_id,
            request_id=request_id,
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind,
            digest=digest,
        ),
    )
    return cipher, nonce, digest


def _decrypt_artifact(row: sqlite3.Row) -> str:
    if row["ciphertext"] is None or row["nonce"] is None:
        raise MailIntelligenceError("One-time verification artifact is unavailable.")
    try:
        raw = _aesgcm()(_vault_key()).decrypt(
            bytes(row["nonce"]),
            bytes(row["ciphertext"]),
            _artifact_aad(
                mail_event_id=str(row["mail_event_id"]),
                request_id=str(row["verification_request_id"]),
                tenant_id=str(row["tenant_id"]),
                user_id=str(row["user_id"]),
                kind=str(row["artifact_kind"]),
                digest=str(row["artifact_digest"]),
            ),
        )
    except Exception as error:
        raise MailIntelligenceError("One-time verification artifact could not be opened.") from error
    if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), str(row["artifact_digest"])):
        raise MailIntelligenceError("One-time verification artifact integrity failed.")
    return raw.decode("utf-8")


def ensure_schema(connection: sqlite3.Connection) -> None:
    ensure_identity_schema(connection)
    ensure_verification_schema(connection)
    production_receipt.ensure_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_events_v2(
          mail_event_id TEXT PRIMARY KEY,
          identity_id TEXT NOT NULL,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          verification_request_id TEXT,
          application_id TEXT,
          job_id INTEGER,
          message_id TEXT NOT NULL,
          envelope_from TEXT NOT NULL,
          envelope_to TEXT NOT NULL,
          sender_domain TEXT,
          router_class TEXT NOT NULL,
          classification TEXT NOT NULL,
          correlation_state TEXT NOT NULL CHECK(correlation_state IN ('NONE','UNIQUE','AMBIGUOUS')),
          subject_redacted TEXT NOT NULL DEFAULT '',
          raw_sha256 TEXT NOT NULL CHECK(length(raw_sha256)=64),
          artifact_digest TEXT,
          received_at TEXT NOT NULL,
          signed_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(identity_id,message_id),
          FOREIGN KEY(identity_id) REFERENCES ats_application_identities(identity_id) ON DELETE RESTRICT,
          FOREIGN KEY(verification_request_id) REFERENCES ats_verification_requests(request_id) ON DELETE SET NULL,
          FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mail_events_v2_application
          ON mail_events_v2(tenant_id,user_id,application_id,received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_mail_events_v2_verification
          ON mail_events_v2(verification_request_id,received_at DESC);

        CREATE TABLE IF NOT EXISTS mail_one_time_artifacts_v2(
          artifact_id TEXT PRIMARY KEY,
          mail_event_id TEXT NOT NULL UNIQUE,
          verification_request_id TEXT NOT NULL UNIQUE,
          application_key TEXT,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          artifact_kind TEXT NOT NULL CHECK(artifact_kind IN (
            'EMAIL_VERIFICATION_LINK','EMAIL_VERIFICATION_CODE','PASSWORD_RESET_LINK','MAGIC_LOGIN_LINK'
          )),
          artifact_digest TEXT NOT NULL CHECK(length(artifact_digest)=64),
          ciphertext BLOB,
          nonce BLOB,
          algorithm_version TEXT NOT NULL,
          state TEXT NOT NULL CHECK(state IN ('READY','CLAIMED','CONSUMED','EXPIRED','ISSUE')),
          claim_token_hash TEXT,
          lease_expires_at TEXT,
          expires_at TEXT NOT NULL,
          created_at TEXT NOT NULL,
          claimed_at TEXT,
          consumed_at TEXT,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(mail_event_id) REFERENCES mail_events_v2(mail_event_id) ON DELETE CASCADE,
          FOREIGN KEY(verification_request_id) REFERENCES ats_verification_requests(request_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_mail_artifact_ready_v2
          ON mail_one_time_artifacts_v2(state,expires_at);

        CREATE TABLE IF NOT EXISTS application_mail_confirmation_evidence_v2(
          evidence_id TEXT PRIMARY KEY,
          mail_event_id TEXT NOT NULL UNIQUE,
          application_id TEXT NOT NULL,
          receipt_id TEXT NOT NULL,
          job_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          sender_domain TEXT,
          binding_json TEXT NOT NULL,
          confirmed_at TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(mail_event_id) REFERENCES mail_events_v2(mail_event_id) ON DELETE RESTRICT,
          FOREIGN KEY(receipt_id) REFERENCES production_submission_receipts_v1(receipt_id) ON DELETE RESTRICT,
          FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE RESTRICT
        );
        CREATE INDEX IF NOT EXISTS idx_application_mail_confirmation_v2
          ON application_mail_confirmation_evidence_v2(application_id,confirmed_at DESC);

        CREATE TABLE IF NOT EXISTS application_communication_events_v2(
          communication_id TEXT PRIMARY KEY,
          mail_event_id TEXT NOT NULL UNIQUE,
          application_id TEXT,
          job_id INTEGER,
          category TEXT NOT NULL CHECK(category IN ('RECRUITER','INTERVIEW','ASSESSMENT','REJECTION','OFFER')),
          sender_domain TEXT,
          received_at TEXT NOT NULL,
          auto_reply_allowed INTEGER NOT NULL DEFAULT 0 CHECK(auto_reply_allowed=0),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(mail_event_id) REFERENCES mail_events_v2(mail_event_id) ON DELETE RESTRICT,
          FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE SET NULL
        );
        """
    )


def _expected_hosts(row: sqlite3.Row) -> set[str]:
    try:
        decoded = json.loads(str(row["expected_hosts_json"]))
    except json.JSONDecodeError:
        return set()
    if not isinstance(decoded, list):
        return set()
    return {str(value).strip().lower().rstrip(".") for value in decoded if str(value).strip()}


def _choose_verification_artifact(
    *,
    classification: dict[str, Any],
    request_row: sqlite3.Row,
) -> tuple[str, str, str | None] | None:
    kind = str(classification["classification"])
    if kind == "EMAIL_VERIFICATION_CODE":
        codes = list(classification.get("codes") or [])
        if len(codes) != 1:
            return None
        return kind, str(codes[0]), None
    if kind not in {"EMAIL_VERIFICATION_LINK", "PASSWORD_RESET_LINK", "MAGIC_LOGIN_LINK"}:
        return None
    expected = _expected_hosts(request_row)
    accepted: list[tuple[str, str]] = []
    for link in classification.get("links") or []:
        host = (urlparse(str(link)).hostname or "").lower().rstrip(".")
        if host and host in expected:
            accepted.append((str(link), host))
    deduped = list(dict.fromkeys(accepted))
    if len(deduped) != 1:
        return None
    return kind, deduped[0][0], deduped[0][1]


def _receipt_candidates(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    received_at: datetime,
) -> list[sqlite3.Row]:
    rows = connection.execute(
        """
        SELECT r.receipt_id,r.application_id,r.provider,r.provider_application_id,
               r.verified_at,p.job_id,j.company_name,j.title
        FROM production_submission_receipts_v1 r
        JOIN application_plans_v2 p ON p.plan_id=r.plan_id
        JOIN jobs j ON j.id=p.job_id
        WHERE r.tenant_id=? AND r.user_id=?
        ORDER BY r.verified_at DESC
        LIMIT 50
        """,
        (tenant_id, user_id),
    ).fetchall()
    result: list[sqlite3.Row] = []
    for row in rows:
        try:
            verified = _parse_time(row["verified_at"], "Receipt time")
        except MailIntelligenceError:
            continue
        delta = received_at - verified
        if timedelta(minutes=-5) <= delta <= timedelta(hours=72):
            result.append(row)
    return result


def _tokens(value: object) -> set[str]:
    stop = {"the", "and", "for", "with", "from", "inc", "llc", "corp", "company", "role", "job"}
    return {
        token for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) >= 3 and token not in stop
    }


def _candidate_bindings(row: sqlite3.Row, *, sender_domain: str | None, text: str) -> dict[str, bool]:
    lowered = text.lower()
    sender = (sender_domain or "").lower()
    company_tokens = _tokens(row["company_name"])
    title_tokens = _tokens(row["title"])
    provider = str(row["provider"] or "").lower()
    provider_id = str(row["provider_application_id"] or "").strip().lower()
    employer_match = any(token in lowered or token in sender for token in company_tokens)
    ats_match = bool(provider and (provider in lowered or provider in sender)) or bool(
        provider_id and provider_id in lowered
    )
    title_hits = sum(1 for token in title_tokens if token in lowered)
    job_match = title_hits >= min(2, max(1, len(title_tokens))) if title_tokens else False
    return {
        "alias_owner": True,
        "time_window": True,
        "employer_or_sender": employer_match,
        "ats_context": ats_match,
        "job_context": job_match,
    }


def _unique_application_correlation(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    received_at: datetime,
    sender_domain: str | None,
    text: str,
) -> tuple[sqlite3.Row, dict[str, bool]] | None:
    qualified: list[tuple[sqlite3.Row, dict[str, bool]]] = []
    for row in _receipt_candidates(
        connection,
        tenant_id=tenant_id,
        user_id=user_id,
        received_at=received_at,
    ):
        bindings = _candidate_bindings(row, sender_domain=sender_domain, text=text)
        contextual = sum(
            bool(bindings[key])
            for key in ("employer_or_sender", "ats_context", "job_context")
        )
        if contextual >= 2:
            qualified.append((row, bindings))
    return qualified[0] if len(qualified) == 1 else None


def _expire_artifacts(connection: sqlite3.Connection, *, now: datetime) -> int:
    return connection.execute(
        """
        UPDATE mail_one_time_artifacts_v2
        SET state='EXPIRED',ciphertext=NULL,nonce=NULL,claim_token_hash=NULL,
            lease_expires_at=NULL,updated_at=?
        WHERE state IN ('READY','CLAIMED') AND expires_at<=?
        """,
        (_iso(now), _iso(now)),
    ).rowcount


def store_verified_message_v2(
    *,
    recipient: str,
    sender: str,
    message_id: str,
    signed_at: str,
    raw_sha256: str,
    parsed: dict[str, Any],
    router_class: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    received = now or _now()
    fine = classify_message(parsed, router_class=router_class)
    classification = str(fine["classification"])
    if classification not in FINE_CLASSES:
        raise MailIntelligenceError("Mail classification is invalid.")
    sender_domain = mail_v1._sender_domain(sender)
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        _expire_artifacts(connection, now=received)
        identity = mail_v1._identity_row(connection, recipient)
        if identity is None or str(identity["relay_state"]) != "ACTIVE":
            raise LookupError("Managed recipient is unavailable.")
        existing = connection.execute(
            "SELECT * FROM mail_events_v2 WHERE identity_id=? AND message_id=?",
            (identity["identity_id"], message_id),
        ).fetchone()
        if existing is not None:
            if not hmac.compare_digest(str(existing["raw_sha256"]), raw_sha256):
                raise mail_v1.InboundMailError("Message-ID was reused with different content.")
            connection.commit()
            return {
                "mail_event_id": existing["mail_event_id"],
                "duplicate": True,
                "classification": existing["classification"],
                "correlation_state": existing["correlation_state"],
                "verification_ready": bool(existing["artifact_digest"]),
                "application_email_confirmed": bool(
                    connection.execute(
                        "SELECT 1 FROM application_mail_confirmation_evidence_v2 WHERE mail_event_id=?",
                        (existing["mail_event_id"],),
                    ).fetchone()
                ),
                "submission_triggered": False,
            }

        tenant_id = str(identity["tenant_id"])
        user_id = str(identity["user_id"])
        verification_request_id: str | None = None
        application_id: str | None = None
        job_id: int | None = None
        artifact_digest: str | None = None
        correlation_state = "NONE"
        verification_ready = False
        confirmation = False
        text = _combined_text(parsed)
        candidates = mail_v1._live_verification_candidates(
            connection,
            tenant_id=tenant_id,
            user_id=user_id,
            recipient=recipient,
            sender_domain=sender_domain,
            now=received,
        )
        request_row: sqlite3.Row | None = None
        if len(candidates) == 1:
            verification_request_id = str(candidates[0]["request_id"])
            request_row = connection.execute(
                "SELECT * FROM ats_verification_requests WHERE request_id=?",
                (verification_request_id,),
            ).fetchone()
            correlation_state = "UNIQUE"
        elif len(candidates) > 1:
            correlation_state = "AMBIGUOUS"

        mail_event_id = f"mailv2_{uuid4().hex}"
        artifact: tuple[str, str, str | None] | None = None
        if request_row is not None:
            if classification == "SECURITY_INTERVENTION":
                connection.execute(
                    """
                    UPDATE ats_verification_requests
                    SET state='SECURITY_CHECKPOINT',sender_domain=?,reason_code='SECURITY_INTERVENTION',updated_at=?
                    WHERE request_id=? AND state='WAITING'
                    """,
                    (sender_domain, _iso(received), verification_request_id),
                )
            else:
                artifact = _choose_verification_artifact(
                    classification=fine,
                    request_row=request_row,
                )
                if artifact is None and classification in ARTIFACT_CLASSES:
                    connection.execute(
                        """
                        UPDATE ats_verification_requests
                        SET state='REJECTED',sender_domain=?,reason_code='AMBIGUOUS_OR_INVALID_EMAIL_ARTIFACT',updated_at=?
                        WHERE request_id=? AND state='WAITING'
                        """,
                        (sender_domain, _iso(received), verification_request_id),
                    )

        connection.execute(
            """
            INSERT INTO mail_events_v2(
              mail_event_id,identity_id,tenant_id,user_id,verification_request_id,
              application_id,job_id,message_id,envelope_from,envelope_to,sender_domain,
              router_class,classification,correlation_state,subject_redacted,raw_sha256,
              artifact_digest,received_at,signed_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                mail_event_id,
                identity["identity_id"],
                tenant_id,
                user_id,
                verification_request_id,
                None,
                None,
                message_id,
                sender,
                recipient,
                sender_domain,
                _safe_router_class(router_class),
                classification,
                correlation_state,
                _redacted_subject(parsed.get("subject"), classification),
                raw_sha256,
                None,
                _iso(received),
                signed_at,
            ),
        )

        if artifact is not None and request_row is not None and verification_request_id is not None:
            artifact_kind, artifact_value, link_host = artifact
            cipher, nonce, artifact_digest = _encrypt_artifact(
                artifact_value,
                mail_event_id=mail_event_id,
                request_id=verification_request_id,
                tenant_id=tenant_id,
                user_id=user_id,
                kind=artifact_kind,
            )
            request_expiry = _parse_time(request_row["expires_at"], "Verification expiry")
            expires_at = min(
                request_expiry,
                received + timedelta(minutes=ARTIFACT_MAX_TTL_MINUTES),
            )
            connection.execute(
                """
                INSERT INTO mail_one_time_artifacts_v2(
                  artifact_id,mail_event_id,verification_request_id,application_key,
                  tenant_id,user_id,artifact_kind,artifact_digest,ciphertext,nonce,
                  algorithm_version,state,expires_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'READY',?,?,?)
                """,
                (
                    f"mailart_{uuid4().hex}",
                    mail_event_id,
                    verification_request_id,
                    request_row["application_key"],
                    tenant_id,
                    user_id,
                    artifact_kind,
                    artifact_digest,
                    cipher,
                    nonce,
                    ARTIFACT_ALGORITHM,
                    _iso(expires_at),
                    _iso(received),
                    _iso(received),
                ),
            )
            connection.execute(
                "UPDATE mail_events_v2 SET artifact_digest=? WHERE mail_event_id=?",
                (artifact_digest, mail_event_id),
            )
            reason = (
                "EXPECTED_ORDINARY_EMAIL_CODE"
                if artifact_kind == "EMAIL_VERIFICATION_CODE"
                else "EXPECTED_ORDINARY_EMAIL_VERIFICATION"
            )
            connection.execute(
                """
                UPDATE ats_verification_requests
                SET state='LINK_READY',sender_domain=?,observed_link_host=?,reason_code=?,updated_at=?
                WHERE request_id=? AND state='WAITING'
                """,
                (sender_domain, link_host, reason, _iso(received), verification_request_id),
            )
            verification_ready = True

        if classification == "APPLICATION_CONFIRMATION":
            correlated = _unique_application_correlation(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                received_at=received,
                sender_domain=sender_domain,
                text=text,
            )
            if correlated is not None:
                receipt, bindings = correlated
                application_id = str(receipt["application_id"])
                job_id = int(receipt["job_id"])
                connection.execute(
                    """
                    INSERT INTO application_mail_confirmation_evidence_v2(
                      evidence_id,mail_event_id,application_id,receipt_id,job_id,provider,
                      sender_domain,binding_json,confirmed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"mailconf_{uuid4().hex}",
                        mail_event_id,
                        application_id,
                        receipt["receipt_id"],
                        job_id,
                        receipt["provider"],
                        sender_domain,
                        json.dumps(bindings, sort_keys=True, separators=(",", ":")),
                        _iso(received),
                    ),
                )
                connection.execute(
                    "UPDATE mail_events_v2 SET application_id=?,job_id=?,correlation_state='UNIQUE' WHERE mail_event_id=?",
                    (application_id, job_id, mail_event_id),
                )
                connection.execute(
                    """
                    INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
                    VALUES(?, 'APPLICATION_EMAIL_CONFIRMED', 'mail-intelligence-v2', 'recorded', ?)
                    """,
                    (
                        job_id,
                        json.dumps(
                            {
                                "application_id": application_id,
                                "mail_event_id": mail_event_id,
                                "receipt_id": receipt["receipt_id"],
                                "provider": receipt["provider"],
                                "submission_authority": False,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                confirmation = True

        if classification in COMMUNICATION_CLASSES:
            correlated = _unique_application_correlation(
                connection,
                tenant_id=tenant_id,
                user_id=user_id,
                received_at=received,
                sender_domain=sender_domain,
                text=text,
            )
            if correlated is not None:
                receipt, _bindings = correlated
                application_id = str(receipt["application_id"])
                job_id = int(receipt["job_id"])
                connection.execute(
                    "UPDATE mail_events_v2 SET application_id=?,job_id=?,correlation_state='UNIQUE' WHERE mail_event_id=?",
                    (application_id, job_id, mail_event_id),
                )
            connection.execute(
                """
                INSERT INTO application_communication_events_v2(
                  communication_id,mail_event_id,application_id,job_id,category,
                  sender_domain,received_at,auto_reply_allowed
                ) VALUES(?,?,?,?,?,?,?,0)
                """,
                (
                    f"comm_{uuid4().hex}",
                    mail_event_id,
                    application_id,
                    job_id,
                    classification,
                    sender_domain,
                    _iso(received),
                ),
            )
            if job_id is not None:
                connection.execute(
                    """
                    INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
                    VALUES(?, 'APPLICATION_COMMUNICATION_RECEIVED', 'mail-intelligence-v2', 'recorded', ?)
                    """,
                    (
                        job_id,
                        json.dumps(
                            {
                                "application_id": application_id,
                                "category": classification,
                                "mail_event_id": mail_event_id,
                                "auto_reply_allowed": False,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )

        connection.commit()
        return {
            "mail_event_id": mail_event_id,
            "duplicate": False,
            "classification": classification,
            "correlation_state": correlation_state,
            "verification_ready": verification_ready,
            "application_email_confirmed": confirmation,
            "submission_triggered": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _artifact_service_secret() -> bytes:
    value = str(os.getenv("MUNSHI_MAIL_ARTIFACT_HMAC_SECRET") or "").encode("utf-8")
    if len(value) < 32:
        raise MailIntelligenceError("Mail artifact service secret is not configured.")
    return value


def _service_material(*, action: str, timestamp_value: str, request_id: str, application_key: str) -> bytes:
    return "\n".join(("v1", action, timestamp_value, request_id, application_key)).encode("utf-8")


def _verify_service_signature(
    *,
    action: str,
    timestamp_value: str,
    request_id: str,
    application_key: str,
    signature: str,
    now: float | None = None,
) -> None:
    try:
        timestamp = int(timestamp_value)
    except (TypeError, ValueError) as error:
        raise PermissionError("Invalid mail-artifact timestamp.") from error
    current = time.time() if now is None else float(now)
    if abs(current - timestamp) > SERVICE_TIMESTAMP_SKEW_SECONDS:
        raise PermissionError("Mail-artifact timestamp is outside the allowed window.")
    match = _SERVICE_SIGNATURE.fullmatch(str(signature or "").strip())
    if match is None:
        raise PermissionError("Invalid mail-artifact signature.")
    expected = hmac.new(
        _artifact_service_secret(),
        _service_material(
            action=action,
            timestamp_value=timestamp_value,
            request_id=request_id,
            application_key=application_key,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise PermissionError("Invalid mail-artifact signature.")


def claim_artifact(
    *,
    request_id: str,
    application_key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        _expire_artifacts(connection, now=current)
        row = connection.execute(
            "SELECT * FROM mail_one_time_artifacts_v2 WHERE verification_request_id=? AND application_key=?",
            (request_id, application_key),
        ).fetchone()
        if row is None:
            raise LookupError("One-time verification artifact is unavailable.")
        state = str(row["state"])
        reclaim = False
        if state == "CLAIMED" and row["lease_expires_at"]:
            reclaim = _parse_time(row["lease_expires_at"], "Artifact lease") <= current
        if state != "READY" and not reclaim:
            raise MailIntelligenceError("One-time verification artifact is not claimable.")
        claim_token = secrets.token_urlsafe(32)
        claim_hash = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()
        lease = current + timedelta(seconds=ARTIFACT_LEASE_SECONDS)
        connection.execute(
            """
            UPDATE mail_one_time_artifacts_v2
            SET state='CLAIMED',claim_token_hash=?,claimed_at=?,lease_expires_at=?,updated_at=?
            WHERE artifact_id=?
            """,
            (claim_hash, _iso(current), _iso(lease), _iso(current), row["artifact_id"]),
        )
        value = _decrypt_artifact(row)
        connection.commit()
        return {
            "request_id": request_id,
            "artifact_kind": row["artifact_kind"],
            "artifact_digest": row["artifact_digest"],
            "artifact": value,
            "claim_token": claim_token,
            "lease_expires_at": _iso(lease),
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def consume_artifact(
    *,
    request_id: str,
    application_key: str,
    claim_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or _now()
    claim_hash = hashlib.sha256(str(claim_token or "").encode("utf-8")).hexdigest()
    connection = get_connection()
    try:
        ensure_schema(connection)
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM mail_one_time_artifacts_v2 WHERE verification_request_id=? AND application_key=?",
            (request_id, application_key),
        ).fetchone()
        if row is None:
            raise LookupError("One-time verification artifact is unavailable.")
        if str(row["state"]) != "CLAIMED" or not hmac.compare_digest(
            str(row["claim_token_hash"] or ""), claim_hash
        ):
            raise PermissionError("One-time verification claim is invalid.")
        changed = connection.execute(
            """
            UPDATE mail_one_time_artifacts_v2
            SET state='CONSUMED',ciphertext=NULL,nonce=NULL,claim_token_hash=NULL,
                lease_expires_at=NULL,consumed_at=?,updated_at=?
            WHERE artifact_id=? AND state='CLAIMED' AND claim_token_hash=?
            """,
            (_iso(current), _iso(current), row["artifact_id"], claim_hash),
        ).rowcount
        if changed != 1:
            raise PermissionError("One-time verification claim lost its consume race.")
        connection.execute(
            """
            UPDATE ats_verification_requests
            SET state='CONSUMED',reason_code='VERIFICATION_ARTIFACT_CONSUMED',updated_at=?
            WHERE request_id=? AND state='LINK_READY'
            """,
            (_iso(current), request_id),
        )
        connection.commit()
        return {
            "request_id": request_id,
            "state": "CONSUMED",
            "artifact_retained": False,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def application_mail_evidence(application_id: str) -> dict[str, Any]:
    connection = get_connection()
    try:
        ensure_schema(connection)
        confirmation = connection.execute(
            """
            SELECT evidence_id,receipt_id,provider,sender_domain,confirmed_at
            FROM application_mail_confirmation_evidence_v2
            WHERE application_id=? ORDER BY confirmed_at DESC LIMIT 1
            """,
            (str(application_id),),
        ).fetchone()
        communications = connection.execute(
            """
            SELECT communication_id,category,sender_domain,received_at
            FROM application_communication_events_v2
            WHERE application_id=? ORDER BY received_at DESC
            """,
            (str(application_id),),
        ).fetchall()
        return {
            "application_id": str(application_id),
            "employer_email_confirmed": confirmation is not None,
            "confirmation": dict(confirmation) if confirmation is not None else None,
            "communications": [dict(row) for row in communications],
        }
    finally:
        connection.close()


async def inbound_mail_v2(
    request: Request,
    x_munshi_version: str | None = Header(default=None, alias="X-Munshi-Version"),
    x_munshi_timestamp: str | None = Header(default=None, alias="X-Munshi-Timestamp"),
    x_munshi_recipient: str | None = Header(default=None, alias="X-Munshi-Recipient"),
    x_munshi_sender: str | None = Header(default=None, alias="X-Munshi-Sender"),
    x_munshi_message_id: str | None = Header(default=None, alias="X-Munshi-Message-ID"),
    x_munshi_mail_class: str | None = Header(default=None, alias="X-Munshi-Mail-Class"),
    x_munshi_signature: str | None = Header(default=None, alias="X-Munshi-Signature"),
) -> dict[str, Any]:
    media_type = str(request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if media_type != "message/rfc822":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Inbound mail requires message/rfc822.",
        )
    try:
        version = mail_v1._safe_metadata("protocol version", x_munshi_version, maximum=16)
        if version != PROTOCOL_VERSION:
            raise PermissionError("Unsupported inbound mail protocol version.")
        timestamp_value = mail_v1._safe_metadata("timestamp", x_munshi_timestamp, maximum=32)
        recipient = mail_v1._safe_metadata("recipient", x_munshi_recipient, maximum=320).lower()
        sender = mail_v1._safe_metadata("sender", x_munshi_sender, maximum=320)
        message_id = mail_v1._safe_metadata("Message-ID", x_munshi_message_id)
        router_class = _safe_router_class(x_munshi_mail_class)
        signature = mail_v1._safe_metadata("signature", x_munshi_signature, maximum=128)
        raw = await mail_v1._read_bounded_body(request)
        signed_at, digest = verify_signature_v2(
            timestamp_value=timestamp_value,
            recipient=recipient,
            sender=sender,
            message_id=message_id,
            router_class=router_class,
            signature=signature,
            raw=raw,
        )
        parsed = mail_v1.parse_message(raw, signed_message_id=message_id)
        result = store_verified_message_v2(
            recipient=recipient,
            sender=sender,
            message_id=message_id,
            signed_at=signed_at,
            raw_sha256=digest,
            parsed=parsed,
            router_class=router_class,
        )
    except HTTPException:
        raise
    except mail_v1.InboundMailConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from None
    except OverflowError as error:
        raise HTTPException(status_code=413, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except (mail_v1.InboundMailError, MailIntelligenceError) as error:
        code = 409 if "reused with different content" in str(error) else 400
        raise HTTPException(status_code=code, detail=str(error)) from None
    return {"success": True, **result}


async def claim_mail_artifact_v2(
    request: Request,
    x_munshi_timestamp: str | None = Header(default=None, alias="X-Munshi-Timestamp"),
    x_munshi_signature: str | None = Header(default=None, alias="X-Munshi-Signature"),
) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Mail artifact request must be an object.")
    request_id = str(payload.get("request_id") or "").strip()
    application_key = str(payload.get("application_key") or "").strip()
    if not request_id or not application_key:
        raise HTTPException(status_code=400, detail="Mail artifact request binding is incomplete.")
    try:
        timestamp_value = mail_v1._safe_metadata("timestamp", x_munshi_timestamp, maximum=32)
        signature = mail_v1._safe_metadata("signature", x_munshi_signature, maximum=128)
        _verify_service_signature(
            action="claim",
            timestamp_value=timestamp_value,
            request_id=request_id,
            application_key=application_key,
            signature=signature,
        )
        return {"success": True, **claim_artifact(request_id=request_id, application_key=application_key)}
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except MailIntelligenceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


async def consume_mail_artifact_v2(
    request: Request,
    x_munshi_timestamp: str | None = Header(default=None, alias="X-Munshi-Timestamp"),
    x_munshi_signature: str | None = Header(default=None, alias="X-Munshi-Signature"),
) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Mail artifact request must be an object.")
    request_id = str(payload.get("request_id") or "").strip()
    application_key = str(payload.get("application_key") or "").strip()
    claim_token = str(payload.get("claim_token") or "").strip()
    if not request_id or not application_key or not claim_token:
        raise HTTPException(status_code=400, detail="Mail artifact consume binding is incomplete.")
    try:
        timestamp_value = mail_v1._safe_metadata("timestamp", x_munshi_timestamp, maximum=32)
        signature = mail_v1._safe_metadata("signature", x_munshi_signature, maximum=128)
        _verify_service_signature(
            action="consume",
            timestamp_value=timestamp_value,
            request_id=request_id,
            application_key=application_key,
            signature=signature,
        )
        return {
            "success": True,
            **consume_artifact(
                request_id=request_id,
                application_key=application_key,
                claim_token=claim_token,
            ),
        }
    except PermissionError as error:
        raise HTTPException(status_code=401, detail=str(error)) from None
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from None
    except MailIntelligenceError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
