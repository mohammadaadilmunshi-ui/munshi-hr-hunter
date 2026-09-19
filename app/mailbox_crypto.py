"""Hunter-owned encryption boundary for mailbox verification content."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALGORITHM = "aes-gcm-v1"
_CONTEXT = b"munshi-mailbox-content-v1"


class MailboxCryptoError(RuntimeError):
    pass


def _vault_key() -> bytes:
    try:
        raw = base64.urlsafe_b64decode(str(os.getenv("MUNSHI_VAULT_KEY") or "").encode())
    except Exception as error:
        raise MailboxCryptoError("Mailbox encryption key is unavailable.") from error
    if len(raw) != 32:
        raise MailboxCryptoError("Mailbox encryption key is unavailable.")
    return raw


def _key() -> bytes:
    return hmac.new(_vault_key(), _CONTEXT, hashlib.sha256).digest()


def _aad(*, identity_id: str, inbound_id: str, raw_sha256: str) -> bytes:
    return json.dumps(
        {
            "v": 1,
            "identity_id": identity_id,
            "inbound_id": inbound_id,
            "raw_sha256": raw_sha256,
            "algorithm": ALGORITHM,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def encrypt_content(
    *,
    identity_id: str,
    inbound_id: str,
    raw_sha256: str,
    subject: str | None,
    headers: object,
    text_body: str | None,
    html_body: str | None,
) -> tuple[bytes, bytes, str]:
    payload = json.dumps(
        {
            "subject": str(subject or ""),
            "headers": headers if isinstance(headers, list) else [],
            "text_body": text_body,
            "html_body": html_body,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(
        nonce,
        payload,
        _aad(identity_id=identity_id, inbound_id=inbound_id, raw_sha256=raw_sha256),
    )
    return ciphertext, nonce, ALGORITHM


def decrypt_content(
    *,
    identity_id: str,
    inbound_id: str,
    raw_sha256: str,
    ciphertext: bytes,
    nonce: bytes,
    algorithm: str,
) -> dict[str, Any]:
    if algorithm != ALGORITHM or len(nonce) != 12 or not ciphertext:
        raise MailboxCryptoError("Mailbox encrypted content is invalid.")
    try:
        raw = AESGCM(_key()).decrypt(
            nonce,
            ciphertext,
            _aad(identity_id=identity_id, inbound_id=inbound_id, raw_sha256=raw_sha256),
        )
        decoded = json.loads(raw)
    except Exception as error:
        raise MailboxCryptoError("Mailbox encrypted content could not be decrypted.") from error
    if not isinstance(decoded, dict):
        raise MailboxCryptoError("Mailbox encrypted content is invalid.")
    return decoded


def runtime_ready() -> bool:
    try:
        _key()
        return True
    except MailboxCryptoError:
        return False
