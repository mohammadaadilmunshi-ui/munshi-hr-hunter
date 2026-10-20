"""Import exact-job employer URL evidence signed by an independent authority.

The Hunter service does not own a private signing key and never signs proposals.
Only administrator-supplied, externally signed evidence can become visible on
LinkedIn job cards. It never changes application submissions or jobs.apply_url.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.linkedin_employer_link_resolver_v1 import LinkResolution
from app.linkedin_verified_link_proof_v5 import verify_signed_link_proof
from app.linkedin_verified_url_evidence_v5 import (
    persist_verified_evidence, validate_verified_evidence,
)

_SCHEMA = "munshi-external-signed-link-import-v1"
_FIELDS = {"job_id", "resolution", "verified_at", "signed_proof"}


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Verification time must be UTC ISO8601")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Verification time must be UTC ISO8601")
    return parsed


def _canonical_job(connection: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = connection.execute(
        "SELECT id,source,company_name,title,job_url,ats_job_id "
        "FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Canonical job no longer exists")
    if isinstance(row, sqlite3.Row):
        return dict(row)
    return dict(zip(("id", "source", "company_name", "title", "job_url", "ats_job_id"), row))


def validate_bundle(
    connection: sqlite3.Connection,
    bundle: Mapping[str, Any],
    *,
    trusted_public_keys: Mapping[str, bytes],
    revoked_proof_ids: frozenset[str] = frozenset(),
    now: datetime | None = None,
    allow_write: bool = False,
) -> dict[str, Any]:
    """Validate every signature before any write. Commit an all-or-none batch."""
    if not isinstance(bundle, Mapping) or set(bundle) != {"schema", "items"}:
        raise ValueError("Signed evidence bundle shape invalid")
    if bundle["schema"] != _SCHEMA:
        raise ValueError("Signed evidence bundle version unsupported")
    items = bundle["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Signed evidence batch size is invalid")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("Signed evidence import requires an aware clock")
    prepared: list[tuple[dict, LinkResolution, datetime, Mapping]] = []
    seen_jobs: set[int] = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != _FIELDS:
            raise ValueError("Signed evidence item shape invalid")
        job_id = item["job_id"]
        if type(job_id) is not int or job_id < 1 or job_id in seen_jobs:
            raise ValueError("Duplicate or invalid canonical job ID")
        seen_jobs.add(job_id)
        fields = item["resolution"]
        if not isinstance(fields, Mapping):
            raise ValueError("Official resolution data missing")
        result = LinkResolution(**fields)
        verified_at = _parse_time(item["verified_at"])
        job = _canonical_job(connection, job_id)
        content = validate_verified_evidence(
            job, result, verified_at=verified_at, now=timestamp
        )
        proof = item["signed_proof"]
        verify_signed_link_proof(
            content, proof, trusted_public_keys,
            now=timestamp, revoked_proof_ids=revoked_proof_ids,
        )
        prepared.append((job, result, verified_at, proof))
    if not allow_write:
        return {
            "validated": len(prepared),
            "inserted": 0,
            "replayed": 0,
            "dry_run": True,
            "submission_authorized": False,
        }
    inserted = replayed = 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for job, result, verified_at, proof in prepared:
            status = persist_verified_evidence(
                connection, job, result,
                verified_at=verified_at, now=timestamp, allow_write=True,
                signed_link_proof=proof,
                trusted_link_public_keys=trusted_public_keys,
                revoked_link_proof_ids=revoked_proof_ids,
            )
            if status["replayed"]:
                replayed += 1
            else:
                inserted += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "validated": len(prepared),
        "inserted": inserted,
        "replayed": replayed,
        "dry_run": False,
        "submission_authorized": False,
    }


def _keys(path: Path) -> dict[str, bytes]:
    """Public trust roots only. Never load a signing private key in Hunter."""
    item = json.loads(path.read_text())
    if not isinstance(item, dict) or not 1 <= len(item) <= 16:
        raise ValueError("Trusted public-key registry is invalid")
    roots = {}
    for name, encoded in item.items():
        if not isinstance(name, str) or not (1 <= len(name) <= 100):
            raise ValueError("Signer key ID invalid")
        if not isinstance(encoded, str):
            raise ValueError("Signer public key invalid")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32:
            raise ValueError("Signer public key must be Ed25519 raw bytes")
        roots[name] = raw
    return roots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--trusted-public-keys", required=True, type=Path)
    parser.add_argument("--revoked-proof-ids", type=Path)
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--acknowledge-signed-import", action="store_true")
    args = parser.parse_args()
    if args.commit and not args.acknowledge_signed_import:
        parser.error("Commit requires explicit --acknowledge-signed-import")
    if args.commit and os.getenv("MUNSHI_SIGNED_LINK_IMPORT_ENABLED") != "true":
        parser.error("Production signed-link import must be explicitly enabled")
    roots = _keys(args.trusted_public_keys)
    bundle = json.loads(args.bundle.read_text())
    revoked = frozenset(
        json.loads(args.revoked_proof_ids.read_text())
        if args.revoked_proof_ids else []
    )
    path = args.db.resolve().as_uri()
    suffix = "?mode=rw" if args.commit else "?mode=ro"
    with sqlite3.connect(path + suffix, uri=True, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        if not args.commit:
            connection.execute("PRAGMA query_only=ON")
        result = validate_bundle(
            connection, bundle, trusted_public_keys=roots,
            revoked_proof_ids=revoked, allow_write=args.commit
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
