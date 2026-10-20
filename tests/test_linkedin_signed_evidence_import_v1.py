from __future__ import annotations

import base64
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.linkedin_employer_link_resolver_v1 import LinkResolution
from app.linkedin_signed_evidence_import_v1 import validate_bundle
from app.linkedin_verified_link_proof_v5 import (
    canonical_content_hash, canonical_proof_message,
)
from app.linkedin_verified_url_evidence_v5 import (
    initialize_schema, validate_verified_evidence, latest_verified_for_job,
)

NOW = datetime(2026, 10, 20, 8, tzinfo=timezone.utc)
JOB = {
    "id": 42,
    "source": "JobSpy/linkedin",
    "company_name": "Example Inc",
    "title": "People Analyst",
    "job_url": "https://www.linkedin.com/jobs/view/4455550001",
    "ats_job_id": "4455550001",
}


def setup():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    initialize_schema(db, allow_migration=True)
    db.execute(
        "CREATE TABLE jobs(id INTEGER PRIMARY KEY,source TEXT,company_name TEXT,"
        "title TEXT,job_url TEXT,ats_job_id TEXT)"
    )
    db.execute(
        "INSERT INTO jobs VALUES(?,?,?,?,?,?)", tuple(JOB.values())
    )
    db.commit()
    return db


def signed_bundle(*, job=None, resolution=None, signed_at=NOW):
    job = job or JOB
    resolution = resolution or LinkResolution(
        linkedin_job_url=JOB["job_url"],
        employer_job_url="https://job-boards.greenhouse.io/example/jobs/987",
        employer_apply_url="https://job-boards.greenhouse.io/example/jobs/987",
        ats_platform="Greenhouse",
        resolution_status="VERIFIED",
        resolution_source="official_public_ats",
        evidence={
            "identity_evidence": "strong",
            "needs_employer_confirmation": False,
            "matching_fields": ["company_name", "title", "location"],
            "submit_authorized": False,
        },
    )
    content = validate_verified_evidence(job, resolution, verified_at=NOW, now=NOW)
    signer = Ed25519PrivateKey.generate()
    public = signer.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    core = {
        "schema": "munshi-linkedin-exact-job-link-proof-v1",
        "key_id": "external-signer-test",
        "content_sha256": canonical_content_hash(content),
        "signed_at": signed_at.isoformat(),
        "expires_at": (signed_at + timedelta(hours=2)).isoformat(),
    }
    proof = dict(core)
    proof["signature"] = base64.urlsafe_b64encode(
        signer.sign(canonical_proof_message(core))
    ).decode("ascii")
    return {
        "schema": "munshi-external-signed-link-import-v1",
        "items": [{
            "job_id": JOB["id"],
            "resolution": resolution.to_dict(),
            "verified_at": NOW.isoformat(),
            "signed_proof": proof,
        }],
    }, {"external-signer-test": public}


def test_readonly_preview_is_zero_write_and_has_no_submission_authority():
    db = setup()
    bundle, keys = signed_bundle()
    db.execute("PRAGMA query_only=ON")
    result = validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW)
    assert result == {
        "validated": 1, "inserted": 0, "replayed": 0,
        "dry_run": True, "submission_authorized": False,
    }
    assert db.execute("SELECT COUNT(*) FROM linkedin_employer_url_evidence_v5").fetchone()[0] == 0


def test_import_is_exact_signed_append_only_idempotent():
    db = setup()
    bundle, keys = signed_bundle()
    result = validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW, allow_write=True)
    assert result["inserted"] == 1
    assert result["submission_authorized"] is False
    verified = latest_verified_for_job(db, JOB, now=NOW)
    assert verified["employer_apply_url"].endswith("/jobs/987")
    assert verified["submit_authorized"] == 0
    replay = validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW, allow_write=True)
    assert replay["replayed"] == 1
    assert db.execute("SELECT COUNT(*) FROM linkedin_employer_url_evidence_v5").fetchone()[0] == 1


def test_forged_destination_or_signer_is_rejected():
    db = setup()
    bundle, keys = signed_bundle()
    bundle["items"][0]["resolution"]["employer_apply_url"] = "https://job-boards.greenhouse.io/example/jobs/evil"
    with pytest.raises(PermissionError, match="does not match"):
        validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW, allow_write=True)
    assert db.execute("SELECT COUNT(*) FROM linkedin_employer_url_evidence_v5").fetchone()[0] == 0

    bundle, keys = signed_bundle()
    with pytest.raises(PermissionError, match="Untrusted"):
        validate_bundle(db, bundle, trusted_public_keys={"different-authority": next(iter(keys.values()))}, now=NOW)


def test_expired_proof_invalid_job_and_duplicates_rejected():
    db = setup()
    bundle, keys = signed_bundle(signed_at=NOW - timedelta(days=2))
    with pytest.raises(PermissionError, match="expired"):
        validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW)
    bundle, keys = signed_bundle()
    bundle["items"][0]["job_id"] = 999
    with pytest.raises(ValueError, match="no longer exists"):
        validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW)
    bundle, keys = signed_bundle()
    bundle["items"].append(bundle["items"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW)


def test_claimed_unsigned_candidate_never_passes_import():
    db = setup()
    bundle, keys = signed_bundle()
    bundle["items"][0]["resolution"]["resolution_status"] = "CANDIDATE"
    with pytest.raises(ValueError, match="Only official-source VERIFIED"):
        validate_bundle(db, bundle, trusted_public_keys=keys, now=NOW)


def test_no_private_signing_key_in_production_importer():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "app" / "linkedin_signed_evidence_import_v1.py").read_text()
    assert "Ed25519PrivateKey" not in source
    assert 'MUNSHI_SIGNED_LINK_IMPORT_ENABLED' in source
    assert "PRAGMA query_only=ON" in source
