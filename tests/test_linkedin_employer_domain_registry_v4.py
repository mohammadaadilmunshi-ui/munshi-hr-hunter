from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.linkedin_employer_domain_registry_v4 import (
    load_signed_registry,
    select_verified_company_site,
)
from tests.test_linkedin_signed_domain_approvals_v4 import valid_signed

NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)


def test_registry_load_is_read_only_and_never_contains_trust_keys(tmp_path):
    record, trust = valid_signed()
    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps(
            {
                "schema": "munshi-approved-employer-domains-v1",
                "approvals": [record],
            }
        )
    )
    before = path.read_bytes()
    rows = load_signed_registry(path)
    selected = select_verified_company_site("Example Co", rows, trust, at=NOW)
    assert selected.official_domain == "example.com"
    assert path.read_bytes() == before


def test_no_company_fails_closed():
    record, trust = valid_signed()
    with pytest.raises(KeyError):
        select_verified_company_site("Other Company", (record,), trust, at=NOW)


def test_tampered_approval_in_manifest_fails_closed():
    record, trust = valid_signed()
    record["site"]["official_domain"] = "attacker.example.com"
    with pytest.raises(ValueError):
        select_verified_company_site("Example Co", (record,), trust, at=NOW)


def test_expired_or_revoked_approval_cannot_be_selected():
    record, trust = valid_signed()
    selected = select_verified_company_site("Example Co", (record,), trust, at=NOW)
    with pytest.raises(ValueError):
        select_verified_company_site(
            "Example Co",
            (record,),
            trust,
            at=NOW,
            revoked_ids=frozenset({selected.approval_reference}),
        )


def test_invalid_manifest_is_rejected(tmp_path):
    path = tmp_path / "data.json"
    path.write_text(json.dumps({"approvals": [], "schema": "wrong"}))
    with pytest.raises(ValueError):
        load_signed_registry(path)


def test_duplicate_signed_approval_fails_closed():
    record, trust = valid_signed()
    with pytest.raises(ValueError, match="Duplicate"):
        select_verified_company_site("Example Co", (record, record), trust, at=NOW)
