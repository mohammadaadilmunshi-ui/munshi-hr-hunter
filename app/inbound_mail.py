"""Authenticated inbound email relay for MUNSHI-managed application aliases.

The Cloudflare Email Worker is the only intended caller. This module verifies a
short-lived HMAC envelope over the exact RFC822 body before resolving any alias,
stores bounded message content, and correlates a message to an application only
when exactly one live verification request proves that relationship.

Inbound mail is deliberately storage/correlation only. It imports no submission,
review-approval, or browser-execution authority and does not mutate verification
request state.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parseaddr
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.ats_managed_identity import ensure_schema as ensure_identity_schema
from app.ats_verification_orchestrator import ensure_schema as ensure_verification_schema
from app.database import get_connection
from app.mailbox_crypto import encrypt_content


router = APIRouter()

PROTOCOL_VERSION = "v1"
MAX_RAW_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_METADATA_BYTES = 998
TIMESTAMP_SKEW_SECONDS = 300
_SIGNATURE = re.compile(r"^v1=([0-9a-f]{64})$")


class InboundMailError(Exception):
    """Non-sensitive inbound-mail validation or persistence failure."""


class InboundMailConfigurationError(RuntimeError):
    """Raised when secure inbound-mail configuration is incomplete."""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Install the isolated inbound mailbox schema and its dependencies."""
    ensure_identity_schema(connection)
    ensure_verification_schema(connection)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS inbound_mail_messages(
          inbound_id TEXT PRIMARY KEY,
          identity_id TEXT NOT NULL,
          tenant_id TEXT NOT NULL,
          user_id TEXT NOT NULL,
          verification_request_id TEXT,
          application_key TEXT,
          correlation_state TEXT NOT NULL
            CHECK(correlation_state IN ('NONE','UNIQUE','AMBIGUOUS')),
          message_id TEXT NOT NULL,
          envelope_from TEXT NOT NULL,
          envelope_to TEXT NOT NULL,
          sender_domain TEXT,
          subject TEXT NOT NULL DEFAULT '',
          source_date TEXT,
          signed_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          raw_sha256 TEXT NOT NULL,
          raw_size INTEGER NOT NULL CHECK(raw_size >= 0),
          headers_json TEXT NOT NULL,
          text_body TEXT,
          html_body TEXT,
          attachment_count INTEGER NOT NULL DEFAULT 0
            CHECK(attachment_count >= 0),
          attachments_json TEXT NOT NULL DEFAULT '[]',
          router_version TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(identity_id,message_id),
          FOREIGN KEY(identity_id)
            REFERENCES ats_application_identities(identity_id) ON DELETE RESTRICT,
          FOREIGN KEY(tenant_id,user_id)
            REFERENCES tenant_memberships(tenant_id,user_id),
          FOREIGN KEY(verification_request_id)
            REFERENCES ats_verification_requests(request_id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_inbound_mail_owner_time
        ON inbound_mail_messages(tenant_id,user_id,received_at DESC);

        CREATE INDEX IF NOT EXISTS idx_inbound_mail_application
        ON inbound_mail_messages(
          tenant_id,user_id,application_key,received_at DESC
        );

        CREATE INDEX IF NOT EXISTS idx_inbound_mail_verification
        ON inbound_mail_messages(verification_request_id,received_at DESC);
        """
    )
    # PRAGMA table_info always exposes the column name at index 1. Numeric
    # indexing works for both sqlite3.Row and the default tuple row shape used
    # by deployment migration runners.
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(inbound_mail_messages)").fetchall()
    }
    if "content_ciphertext" not in columns:
        connection.execute("ALTER TABLE inbound_mail_messages ADD COLUMN content_ciphertext BLOB")
    if "content_nonce" not in columns:
        connection.execute("ALTER TABLE inbound_mail_messages ADD COLUMN content_nonce BLOB")
    if "content_algorithm" not in columns:
        connection.execute("ALTER TABLE inbound_mail_messages ADD COLUMN content_algorithm TEXT")


def _safe_metadata(
    name: str,
    value: str | None,
    *,
    maximum: int = MAX_METADATA_BYTES,
) -> str:
    candidate = str(value or "").strip()
    if not candidate or len(candidate.encode("utf-8")) > maximum:
        raise InboundMailError(f"{name} is missing or too large.")
    if "\r" in candidate or "\n" in candidate or "\x00" in candidate:
        raise InboundMailError(f"{name} contains invalid control characters.")
    return candidate


def _signing_secret() -> bytes:
    value = str(os.getenv("MUNSHI_MAIL_SIGNING_SECRET") or "").strip()
    encoded = value.encode("utf-8")
    if len(encoded) < 32:
        raise InboundMailConfigurationError(
            "Inbound mail signing secret must be at least 32 bytes."
        )
    return encoded


def _signed_at(
    timestamp_value: str,
    *,
    now: float | None = None,
) -> str:
    try:
        timestamp = int(timestamp_value)
    except (TypeError, ValueError) as error:
        raise PermissionError("Invalid inbound mail timestamp.") from error

    current = time.time() if now is None else float(now)
    if abs(current - timestamp) > TIMESTAMP_SKEW_SECONDS:
        raise PermissionError(
            "Inbound mail timestamp is outside the allowed window."
        )
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _body_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_signature_material(
    *,
    timestamp_value: str,
    recipient: str,
    sender: str,
    message_id: str,
    raw_sha256: str,
) -> bytes:
    return "\n".join(
        (
            PROTOCOL_VERSION,
            timestamp_value,
            recipient,
            sender,
            message_id,
            raw_sha256,
        )
    ).encode("utf-8")


def verify_signature(
    *,
    timestamp_value: str,
    recipient: str,
    sender: str,
    message_id: str,
    signature: str,
    raw: bytes,
    now: float | None = None,
) -> tuple[str, str]:
    """Return signed_at and body digest after strict timestamp/HMAC verification."""
    if len(raw) > MAX_RAW_BYTES:
        raise OverflowError("Inbound message exceeds the raw size limit.")

    signed_at = _signed_at(timestamp_value, now=now)
    match = _SIGNATURE.fullmatch(str(signature or "").strip())
    if match is None:
        raise PermissionError("Invalid inbound mail signature.")

    digest = _body_sha256(raw)
    expected = hmac.new(
        _signing_secret(),
        canonical_signature_material(
            timestamp_value=timestamp_value,
            recipient=recipient,
            sender=sender,
            message_id=message_id,
            raw_sha256=digest,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(match.group(1), expected):
        raise PermissionError("Invalid inbound mail signature.")

    return signed_at, digest


def _sender_domain(sender: str) -> str | None:
    address = parseaddr(sender)[1] or str(sender or "").strip()
    if "@" not in address:
        return None
    domain = address.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return domain or None


def _domain_matches(observed: str, expected_parent: str) -> bool:
    observed_domain = str(observed or "").strip().casefold().rstrip(".")
    expected_domain = str(expected_parent or "").strip().casefold().rstrip(".")
    return bool(
        observed_domain
        and expected_domain
        and (
            observed_domain == expected_domain
            or observed_domain.endswith("." + expected_domain)
        )
    )


def _decode_text_part(part: Message) -> str:
    try:
        value = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, UnicodeError):
            return payload.decode("utf-8", errors="replace")

    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return value.decode(charset, errors="replace")
        except (LookupError, UnicodeError):
            return value.decode("utf-8", errors="replace")
    return str(value or "")


def _append_bounded(
    parts: list[str],
    value: str,
    *,
    label: str,
) -> None:
    projected = "\n".join([*parts, value]) if parts else value
    if len(projected.encode("utf-8")) > MAX_TEXT_BYTES:
        raise OverflowError(f"Inbound {label} body exceeds the size limit.")
    parts.append(value)


def _attachment_metadata(
    part: Message,
    *,
    content_type: str,
    disposition: str,
) -> tuple[dict[str, Any], int]:
    payload = part.get_payload(decode=True) or b""
    size = len(payload)
    if size > MAX_ATTACHMENT_BYTES:
        raise OverflowError(
            "Inbound attachment exceeds the per-file size limit."
        )
    return (
        {
            "filename": str(part.get_filename() or ""),
            "content_type": content_type,
            "content_disposition": disposition or None,
            "content_id": str(part.get("content-id") or "").strip() or None,
            "size": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        size,
    )


def parse_message(
    raw: bytes,
    *,
    signed_message_id: str,
) -> dict[str, Any]:
    """Parse bounded RFC822 content without persisting attachment binaries."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as error:
        raise InboundMailError(
            "Inbound RFC822 message could not be parsed."
        ) from error

    raw_message_id = str(message.get("message-id") or "").strip()
    if not raw_message_id or raw_message_id != signed_message_id:
        raise InboundMailError(
            "Signed Message-ID does not match the RFC822 message."
        )

    headers = [
        [str(name), str(value)]
        for name, value in message.raw_items()
    ]
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    total_attachment_bytes = 0

    for part in message.walk():
        if part.is_multipart():
            continue

        filename = part.get_filename()
        disposition = str(part.get_content_disposition() or "").lower()
        content_type = str(
            part.get_content_type() or "application/octet-stream"
        ).lower()

        is_classification_text = (
            filename is None
            and disposition != "attachment"
            and content_type in {"text/plain", "text/html"}
        )
        if is_classification_text:
            if content_type == "text/plain":
                _append_bounded(
                    text_parts,
                    _decode_text_part(part),
                    label="plain-text",
                )
            else:
                _append_bounded(
                    html_parts,
                    _decode_text_part(part),
                    label="HTML",
                )
            continue

        if len(attachments) >= MAX_ATTACHMENTS:
            raise OverflowError("Inbound message has too many attachments.")

        metadata, size = _attachment_metadata(
            part,
            content_type=content_type,
            disposition=disposition,
        )
        total_attachment_bytes += size
        if total_attachment_bytes > MAX_TOTAL_ATTACHMENT_BYTES:
            raise OverflowError(
                "Inbound attachments exceed the total size limit."
            )
        attachments.append(metadata)

    return {
        "subject": str(message.get("subject") or "")[:4096],
        "source_date": str(message.get("date") or "").strip() or None,
        "headers": headers,
        "text_body": "\n".join(text_parts) or None,
        "html_body": "\n".join(html_parts) or None,
        "attachments": attachments,
    }


def _live_verification_candidates(
    connection: sqlite3.Connection,
    *,
    tenant_id: str,
    user_id: str,
    recipient: str,
    sender_domain: str | None,
    now: datetime,
) -> list[sqlite3.Row]:
    if not sender_domain:
        return []

    rows = connection.execute(
        """
        SELECT
          request_id,
          application_key,
          expected_sender_domains_json,
          expires_at
        FROM ats_verification_requests
        WHERE tenant_id=? AND user_id=?
          AND recipient_email=? COLLATE NOCASE
          AND state='WAITING'
        """,
        (tenant_id, user_id, recipient),
    ).fetchall()

    matches: list[sqlite3.Row] = []
    for row in rows:
        try:
            expires_at = datetime.fromisoformat(
                str(row["expires_at"]).replace("Z", "+00:00")
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)

            decoded = json.loads(
                str(row["expected_sender_domains_json"])
            )
            if not isinstance(decoded, list):
                continue
            expected = {
                str(value).strip().lower().rstrip(".")
                for value in decoded
                if str(value).strip()
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

        if expires_at > now and any(
            _domain_matches(sender_domain, expected_domain)
            for expected_domain in expected
        ):
            matches.append(row)

    return matches


def _identity_row(
    connection: sqlite3.Connection,
    recipient: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT identity_id,tenant_id,user_id,email_address,relay_state
        FROM ats_application_identities
        WHERE email_address=? COLLATE NOCASE
        LIMIT 1
        """,
        (recipient,),
    ).fetchone()


def _duplicate_result(
    existing: sqlite3.Row,
    *,
    raw_sha256: str,
) -> dict[str, Any]:
    if not hmac.compare_digest(
        str(existing["raw_sha256"]),
        raw_sha256,
    ):
        raise InboundMailError(
            "Message-ID was reused with different content."
        )
    return {
        "inbound_id": existing["inbound_id"],
        "duplicate": True,
        "application_key": existing["application_key"],
        "correlation_state": existing["correlation_state"],
        "submission_triggered": False,
    }


def store_verified_message(
    *,
    recipient: str,
    sender: str,
    message_id: str,
    signed_at: str,
    raw_sha256: str,
    raw: bytes,
    parsed: dict[str, Any],
    router_version: str = PROTOCOL_VERSION,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist authenticated mail idempotently without application actions."""
    received = now or datetime.now(UTC)
    connection = get_connection()
    transaction_open = False

    try:
        ensure_schema(connection)
        connection.commit()

        connection.execute("BEGIN IMMEDIATE")
        transaction_open = True

        identity = _identity_row(connection, recipient)
        if identity is None or str(identity["relay_state"]) != "ACTIVE":
            raise LookupError("Managed recipient is unavailable.")

        existing = connection.execute(
            """
            SELECT
              inbound_id,
              raw_sha256,
              application_key,
              correlation_state
            FROM inbound_mail_messages
            WHERE identity_id=? AND message_id=?
            """,
            (identity["identity_id"], message_id),
        ).fetchone()
        if existing is not None:
            result = _duplicate_result(
                existing,
                raw_sha256=raw_sha256,
            )
            connection.commit()
            transaction_open = False
            return result

        sender_domain = _sender_domain(sender)
        candidates = _live_verification_candidates(
            connection,
            tenant_id=str(identity["tenant_id"]),
            user_id=str(identity["user_id"]),
            recipient=recipient,
            sender_domain=sender_domain,
            now=received,
        )

        if len(candidates) == 1:
            verification_request_id = candidates[0]["request_id"]
            application_key = candidates[0]["application_key"]
            correlation_state = "UNIQUE"
        elif len(candidates) > 1:
            verification_request_id = None
            application_key = None
            correlation_state = "AMBIGUOUS"
        else:
            verification_request_id = None
            application_key = None
            correlation_state = "NONE"

        inbound_id = str(uuid4())
        content_ciphertext, content_nonce, content_algorithm = encrypt_content(
            identity_id=str(identity["identity_id"]),
            inbound_id=inbound_id,
            raw_sha256=raw_sha256,
            subject=parsed["subject"],
            headers=parsed["headers"],
            text_body=parsed["text_body"],
            html_body=parsed["html_body"],
        )
        connection.execute(
            """
            INSERT INTO inbound_mail_messages(
              inbound_id,
              identity_id,
              tenant_id,
              user_id,
              verification_request_id,
              application_key,
              correlation_state,
              message_id,
              envelope_from,
              envelope_to,
              sender_domain,
              subject,
              source_date,
              signed_at,
              received_at,
              raw_sha256,
              raw_size,
              headers_json,
              text_body,
              html_body,
              attachment_count,
              attachments_json,
              router_version,
              content_ciphertext,
              content_nonce,
              content_algorithm
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                inbound_id,
                identity["identity_id"],
                identity["tenant_id"],
                identity["user_id"],
                verification_request_id,
                application_key,
                correlation_state,
                message_id,
                sender,
                recipient,
                sender_domain,
                "[encrypted]",
                parsed["source_date"],
                signed_at,
                received.isoformat(),
                raw_sha256,
                len(raw),
                "[]",
                None,
                None,
                len(parsed["attachments"]),
                json.dumps(
                    parsed["attachments"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                router_version,
                content_ciphertext,
                content_nonce,
                content_algorithm,
            ),
        )
        connection.commit()
        transaction_open = False
        return {
            "inbound_id": inbound_id,
            "duplicate": False,
            "application_key": application_key,
            "correlation_state": correlation_state,
            "submission_triggered": False,
        }

    except sqlite3.IntegrityError:
        if transaction_open:
            connection.rollback()
            transaction_open = False

        identity = _identity_row(connection, recipient)
        existing = None
        if identity is not None:
            existing = connection.execute(
                """
                SELECT
                  inbound_id,
                  raw_sha256,
                  application_key,
                  correlation_state
                FROM inbound_mail_messages
                WHERE identity_id=? AND message_id=?
                """,
                (identity["identity_id"], message_id),
            ).fetchone()
        if existing is None:
            raise
        return _duplicate_result(
            existing,
            raw_sha256=raw_sha256,
        )
    except Exception:
        if transaction_open:
            connection.rollback()
        raise
    finally:
        connection.close()


async def _read_bounded_body(request: Request) -> bytes:
    header_value = request.headers.get("content-length")
    if header_value:
        try:
            declared = int(header_value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length.",
            ) from None
        if declared < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Content-Length.",
            )
        if declared > MAX_RAW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Inbound message is too large.",
            )

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_RAW_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Inbound message is too large.",
            )
        chunks.append(chunk)

    if size == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inbound message body is empty.",
        )
    return b"".join(chunks)


@router.post("/api/mail/inbound")
async def inbound_mail(
    request: Request,
    x_munshi_version: str | None = Header(
        default=None,
        alias="X-Munshi-Version",
    ),
    x_munshi_timestamp: str | None = Header(
        default=None,
        alias="X-Munshi-Timestamp",
    ),
    x_munshi_recipient: str | None = Header(
        default=None,
        alias="X-Munshi-Recipient",
    ),
    x_munshi_sender: str | None = Header(
        default=None,
        alias="X-Munshi-Sender",
    ),
    x_munshi_message_id: str | None = Header(
        default=None,
        alias="X-Munshi-Message-ID",
    ),
    x_munshi_mail_class: str | None = Header(
        default=None,
        alias="X-Munshi-Mail-Class",
    ),
    x_munshi_signature: str | None = Header(
        default=None,
        alias="X-Munshi-Signature",
    ),
) -> dict[str, Any]:
    """Accept one signed RFC822 message from the MUNSHI mail router."""
    media_type = str(
        request.headers.get("content-type") or ""
    ).split(";", 1)[0].strip().lower()
    if media_type != "message/rfc822":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Inbound mail requires message/rfc822.",
        )

    try:
        version = _safe_metadata(
            "protocol version",
            x_munshi_version,
            maximum=16,
        )
        timestamp_value = _safe_metadata(
            "timestamp",
            x_munshi_timestamp,
            maximum=32,
        )
        recipient = _safe_metadata(
            "recipient",
            x_munshi_recipient,
            maximum=320,
        ).lower()
        sender = _safe_metadata(
            "sender",
            x_munshi_sender,
            maximum=320,
        )
        message_id = _safe_metadata(
            "Message-ID",
            x_munshi_message_id,
        )
        signature = _safe_metadata(
            "signature",
            x_munshi_signature,
            maximum=128,
        )
        if version not in {PROTOCOL_VERSION, "v2"}:
            raise PermissionError(
                "Unsupported inbound mail protocol version."
            )

        _signed_at(timestamp_value)
        raw = await _read_bounded_body(request)
        parsed = parse_message(
            raw,
            signed_message_id=message_id,
        )

        if version == "v2":
            # Import lazily because mail_intelligence_v2 imports this module's
            # bounded parser/signature primitives. V2 is the deployed
            # Cloudflare router protocol and carries its coarse classification
            # inside the signed material.
            from app import mail_intelligence_v2

            router_class = _safe_metadata(
                "mail class",
                x_munshi_mail_class,
                maximum=64,
            )
            signed_at, digest = mail_intelligence_v2.verify_signature_v2(
                timestamp_value=timestamp_value,
                recipient=recipient,
                sender=sender,
                message_id=message_id,
                router_class=router_class,
                signature=signature,
                raw=raw,
            )
            result = mail_intelligence_v2.store_verified_message_v2(
                recipient=recipient,
                sender=sender,
                message_id=message_id,
                signed_at=signed_at,
                raw_sha256=digest,
                parsed=parsed,
                router_class=router_class,
            )
        else:
            signed_at, digest = verify_signature(
                timestamp_value=timestamp_value,
                recipient=recipient,
                sender=sender,
                message_id=message_id,
                signature=signature,
                raw=raw,
            )
            result = store_verified_message(
                recipient=recipient,
                sender=sender,
                message_id=message_id,
                signed_at=signed_at,
                raw_sha256=digest,
                raw=raw,
                parsed=parsed,
                router_version=version,
            )

    except HTTPException:
        raise
    except InboundMailConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from None
    except PermissionError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from None
    except OverflowError as error:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(error),
        ) from None
    except LookupError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from None
    except InboundMailError as error:
        code = (
            status.HTTP_409_CONFLICT
            if "reused with different content" in str(error)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=code,
            detail=str(error),
        ) from None

    return {"success": True, **result}
