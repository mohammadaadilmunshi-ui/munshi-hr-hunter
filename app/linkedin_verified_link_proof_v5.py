"""Verify an externally signed, exact-job employer-link approval before storage.

The signer is a separately authorized process. This module verifies approvals
only. Never generate/store the signing private key in the Hunter runtime.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_SCHEMA = "munshi-linkedin-exact-job-link-proof-v1"
_MAX_PROOF_AGE = timedelta(days=7)


def canonical_content_hash(content: Mapping[str, Any]) -> str:
    """Bind a signature to the *entire* job identity, URL, and provenance."""
    return hashlib.sha256(
        json.dumps(
            dict(content), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
    ).hexdigest()


def canonical_proof_message(record: Mapping[str, Any]) -> bytes:
    if not isinstance(record, Mapping) or set(record) != {
        "schema",
        "key_id",
        "content_sha256",
        "signed_at",
        "expires_at",
    }:
        raise ValueError("Invalid signed link proof fields")
    if record["schema"] != _SCHEMA:
        raise ValueError("Unknown signed link proof schema")
    if not isinstance(record["key_id"], str) or not record["key_id"]:
        raise ValueError("Signed link proof key is missing")
    digest = record["content_sha256"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or not all(c in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("Signed link proof digest malformed")
    for k in ("signed_at", "expires_at"):
        if not isinstance(record[k], str):
            raise TypeError("Proof timestamp must be ISO8601 UTC")
    return json.dumps(
        dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _read_utc(value: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Signed proof has malformed timestamp") from exc
    if ts.tzinfo is None or ts.utcoffset() != timedelta(0):
        raise ValueError("Signed proof timestamps must use UTC")
    return ts.astimezone(timezone.utc)


def verify_signed_link_proof(
    content: Mapping[str, Any],
    proof: Mapping[str, Any] | None,
    trusted_public_keys: Mapping[str, bytes] | None,
    *,
    now: datetime,
    revoked_proof_ids: frozenset[str] = frozenset(),
) -> str:
    """Require an independently signed exact evidence record, fail closed."""
    if not isinstance(proof, Mapping) or set(proof) != {
        "schema",
        "key_id",
        "content_sha256",
        "signed_at",
        "expires_at",
        "signature",
    }:
        raise PermissionError("Signed exact-job link proof is required")
    core = {key: value for key, value in proof.items() if key != "signature"}
    message = canonical_proof_message(core)
    key_id = core["key_id"]
    if not trusted_public_keys or key_id not in trusted_public_keys:
        raise PermissionError("Untrusted link-proof signing authority")
    if core["content_sha256"] != canonical_content_hash(content):
        raise PermissionError(
            "Signed link proof does not match exact job, URL, or evidence"
        )
    current = now.astimezone(timezone.utc) if now.tzinfo else None
    if current is None:
        raise PermissionError("Signed link proof requires timezone-aware clock")
    signed = _read_utc(core["signed_at"])
    expiry = _read_utc(core["expires_at"])
    if expiry <= signed or expiry - signed > _MAX_PROOF_AGE:
        raise PermissionError("Signed link proof has invalid validity window")
    if signed > current + timedelta(minutes=2) or current >= expiry:
        raise PermissionError("Signed link proof expired or not yet valid")
    proof_id = hashlib.sha256(message).hexdigest()
    if proof_id in revoked_proof_ids:
        raise PermissionError("Signed link proof revoked")
    try:
        sig_text = proof["signature"]
        if not isinstance(sig_text, str) or len(sig_text) > 512:
            raise ValueError("Bad signature")
        signature = base64.b64decode(sig_text, altchars=b"-_", validate=True)
        Ed25519PublicKey.from_public_bytes(trusted_public_keys[key_id]).verify(
            signature, message
        )
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise PermissionError("Signed link proof failed verification") from exc
    return proof_id
