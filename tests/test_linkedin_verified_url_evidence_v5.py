from __future__ import annotations

import base64
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.linkedin_employer_link_resolver_v1 import LinkResolution
from app.linkedin_verified_link_proof_v5 import (
    canonical_content_hash,
    canonical_proof_message,
)
from app.linkedin_verified_url_evidence_v5 import (
    destination_buttons,
    initialize_schema,
    latest_verified_for_job,
    validate_verified_evidence,
)
from app.linkedin_verified_url_evidence_v5 import (
    persist_verified_evidence as _persist_verified_evidence,
)

NOW = datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc)


def _sign_content(content, *, ts=NOW):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    core = {
        "schema": "munshi-linkedin-exact-job-link-proof-v1",
        "key_id": "test-operator",
        "content_sha256": canonical_content_hash(content),
        "signed_at": ts.isoformat(),
        "expires_at": (ts + timedelta(days=1)).isoformat(),
    }
    record = {
        **core,
        "signature": base64.urlsafe_b64encode(
            private.sign(canonical_proof_message(core))
        ).decode("ascii"),
    }
    return record, {"test-operator": public}


def persist_verified_evidence(
    connection,
    record,
    result,
    *,
    verified_at,
    now=None,
    allow_write=False,
):
    """Test-only authorized signer: production code has no embedded private key."""
    if not allow_write:
        return _persist_verified_evidence(
            connection,
            record,
            result,
            verified_at=verified_at,
            now=now,
            allow_write=False,
        )
    canonical = validate_verified_evidence(
        record,
        result,
        verified_at=verified_at,
        now=now,
    )
    proof, keys = _sign_content(canonical, ts=now or NOW)
    return _persist_verified_evidence(
        connection,
        record,
        result,
        verified_at=verified_at,
        now=now,
        allow_write=True,
        signed_link_proof=proof,
        trusted_link_public_keys=keys,
    )


def job(**kw):
    return {
        "id": 101,
        "source": "JobSpy/linkedin",
        "company_name": "Example Inc",
        "title": "HR Analyst",
        "ats_job_id": "4455550001",
        "job_url": "https://www.linkedin.com/jobs/view/4455550001",
        "apply_url": None,
        **kw,
    }


def verified(**kw):
    return LinkResolution(
        linkedin_job_url=job()["job_url"],
        employer_job_url="https://job-boards.greenhouse.io/example/jobs/987",
        employer_apply_url="https://job-boards.greenhouse.io/example/jobs/987",
        ats_platform="Greenhouse",
        resolution_status="VERIFIED",
        resolution_source="verified_first_party_careers",
        evidence={
            "identity_evidence": "strong",
            "needs_employer_confirmation": False,
            "employer_approval_reference": "signed-approval-sha",
            "signed_approval_validated": True,
            "ats_board_from_official_website": True,
            "submit_authorized": False,
        },
        **kw,
    )


def setup():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    initialize_schema(c, allow_migration=True)
    c.execute(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY, source TEXT, "
        "company_name TEXT, title TEXT, job_url TEXT, ats_job_id TEXT)"
    )
    sample = job()
    c.execute(
        "INSERT INTO jobs(id, source, company_name, title, job_url, ats_job_id) "
        "VALUES (?,?,?,?,?,?)",
        tuple(
            sample[key]
            for key in (
                "id",
                "source",
                "company_name",
                "title",
                "job_url",
                "ats_job_id",
            )
        ),
    )
    return c


def test_no_schema_mutation_without_permission():
    c = sqlite3.connect(":memory:")
    with pytest.raises(PermissionError):
        initialize_schema(c)
    assert (
        c.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' "
            "AND name='linkedin_employer_url_evidence_v5'"
        ).fetchone()[0]
        == 0
    )


def test_verified_links_are_append_only_and_idempotent():
    c = setup()
    first = persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    assert first["replayed"] is False
    replay = persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    assert replay["replayed"] is True
    assert replay["evidence_id"] == first["evidence_id"]
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 1
    )
    assert latest_verified_for_job(c, job(), now=NOW)["ats_platform"] == "Greenhouse"


def test_no_write_without_explicit_authority():
    c = setup()
    with pytest.raises(PermissionError):
        persist_verified_evidence(c, job(), verified(), verified_at=NOW, now=NOW)
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 0
    )


def test_conflicting_links_require_reconciliation():
    c = setup()
    persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    with pytest.raises(ValueError, match="different currently verified"):
        persist_verified_evidence(
            c,
            job(),
            replace(
                verified(),
                employer_apply_url="https://job-boards.greenhouse.io/example/jobs/988",
                employer_job_url="https://job-boards.greenhouse.io/example/jobs/988",
            ),
            verified_at=NOW,
            now=NOW,
            allow_write=True,
        )
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 1
    )


def test_changing_identity_invalidates_old_evidence():
    c = setup()
    persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    assert latest_verified_for_job(c, job(title="Different role"), now=NOW) is None
    assert (
        latest_verified_for_job(
            c, job(job_url="https://linkedin.com/jobs/view/4455550002"), now=NOW
        )
        is None
    )
    assert latest_verified_for_job(c, job(id=102), now=NOW) is None


def test_expired_link_not_displayed_as_verified():
    c = setup()
    persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    assert latest_verified_for_job(c, job(), now=NOW + timedelta(days=8)) is None


def test_missing_table_does_not_migrate_read_only():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    assert latest_verified_for_job(c, job(), now=NOW) is None


@pytest.mark.parametrize(
    "result",
    [
        replace(verified(), resolution_status="CANDIDATE"),
        replace(verified(), ats_platform=None),
        replace(verified(), resolution_source="stored_cross_source"),
        replace(
            verified(), employer_apply_url="https://www.linkedin.com/jobs/view/123"
        ),
        replace(verified(), employer_apply_url="http://jobs.example.org/foo"),
        replace(
            verified(), linkedin_job_url="https://www.linkedin.com/jobs/view/4455550999"
        ),
        replace(
            verified(),
            evidence={**verified().evidence, "signed_approval_validated": False},
        ),
        replace(
            verified(), evidence={**verified().evidence, "identity_evidence": "partial"}
        ),
        replace(
            verified(), evidence={**verified().evidence, "submit_authorized": True}
        ),
    ],
)
def test_unsafe_or_unauthorized_evidence_is_never_persisted(result):
    with pytest.raises(ValueError):
        validate_verified_evidence(job(), result, verified_at=NOW, now=NOW)


def test_old_future_dated_proof_rejected():
    with pytest.raises(ValueError, match="expired"):
        validate_verified_evidence(
            job(), verified(), verified_at=NOW - timedelta(days=8), now=NOW
        )
    with pytest.raises(ValueError, match="future"):
        validate_verified_evidence(
            job(), verified(), verified_at=NOW + timedelta(days=1), now=NOW
        )


def test_job_provenance_must_be_linkedin():
    with pytest.raises(ValueError):
        validate_verified_evidence(
            job(source="Indeed"),
            verified(),
            verified_at=NOW,
            now=NOW,
        )


def test_job_original_url_never_overwritten():
    record = job()
    original = record.copy()
    validated = validate_verified_evidence(record, verified(), verified_at=NOW, now=NOW)
    assert record == original
    assert validated["linkedin_job_url"] == original["job_url"]
    assert validated["submit_authorized"] == 0


def test_button_projection_protects_unverified_apply():
    c = setup()
    assert destination_buttons(job(), None)["employer_apply_url"] is None
    persist_verified_evidence(
        c, job(), verified(), verified_at=NOW, now=NOW, allow_write=True
    )
    proof = latest_verified_for_job(c, job(), now=NOW)
    links = destination_buttons(job(), proof)
    assert links["linkedin_job_url"] == job()["job_url"]
    assert links["employer_apply_url"] == verified().employer_apply_url
    assert links["prepare_action"] == "INTERNAL_MUNSHI"
    assert links["submit_authorized"] is False
    assert (
        destination_buttons(job(title="Wrong role"), proof)["employer_apply_url"]
        is None
    )


def test_unrelated_non_linkedin_job_is_untouched():
    assert destination_buttons(job(source="Greenhouse"), None) == {"is_linkedin": False}


def test_evidence_write_cannot_override_live_canonical_job():
    c = setup()
    c.execute("UPDATE jobs SET title='Hiring Specialist' WHERE id=101")
    with pytest.raises(ValueError, match="canonical job differs"):
        persist_verified_evidence(
            c,
            job(),
            verified(),
            verified_at=NOW,
            now=NOW,
            allow_write=True,
        )
    assert (
        c.execute("SELECT COUNT(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 0
    )


def test_unsigned_forged_boolean_flag_is_blocked_before_insert():
    c = setup()
    with pytest.raises(PermissionError, match="Signed exact-job"):
        _persist_verified_evidence(
            c,
            job(),
            verified(),
            verified_at=NOW,
            now=NOW,
            allow_write=True,
        )
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 0
    )


def test_changed_employer_url_invalidates_signed_approval():
    c = setup()
    canonical = validate_verified_evidence(job(), verified(), verified_at=NOW, now=NOW)
    signed, keys = _sign_content(canonical)
    modified = replace(
        verified(),
        employer_apply_url="https://job-boards.greenhouse.io/example/jobs/988",
        employer_job_url="https://job-boards.greenhouse.io/example/jobs/988",
    )
    with pytest.raises(PermissionError, match="does not match"):
        _persist_verified_evidence(
            c,
            job(),
            modified,
            verified_at=NOW,
            now=NOW,
            allow_write=True,
            signed_link_proof=signed,
            trusted_link_public_keys=keys,
        )
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 0
    )


def test_untrusted_authority_revocation_and_signature_tampering_blocked():
    c = setup()
    canonical = validate_verified_evidence(job(), verified(), verified_at=NOW, now=NOW)
    signed, keys = _sign_content(canonical)
    variants = [
        (signed, {}, frozenset()),
        ({**signed, "signature": "bad"}, keys, frozenset()),
    ]
    import hashlib

    approval_id = hashlib.sha256(
        canonical_proof_message({k: v for k, v in signed.items() if k != "signature"})
    ).hexdigest()
    variants.append((signed, keys, frozenset({approval_id})))
    for candidate, trust, revoked in variants:
        with pytest.raises(PermissionError):
            _persist_verified_evidence(
                c,
                job(),
                verified(),
                verified_at=NOW,
                now=NOW,
                allow_write=True,
                signed_link_proof=candidate,
                trusted_link_public_keys=trust,
                revoked_link_proof_ids=revoked,
            )
    assert (
        c.execute("SELECT count(*) FROM linkedin_employer_url_evidence_v5").fetchone()[
            0
        ]
        == 0
    )


def test_expired_proof_blocks_submission_independently_of_evidence_expiry():
    c = setup()
    canonical = validate_verified_evidence(job(), verified(), verified_at=NOW, now=NOW)
    signed, keys = _sign_content(canonical, ts=NOW - timedelta(days=2))
    with pytest.raises(PermissionError, match="expired"):
        _persist_verified_evidence(
            c,
            job(),
            verified(),
            verified_at=NOW,
            now=NOW,
            allow_write=True,
            signed_link_proof=signed,
            trusted_link_public_keys=keys,
        )
