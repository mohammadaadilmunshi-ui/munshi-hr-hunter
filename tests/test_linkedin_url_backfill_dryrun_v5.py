from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection
from scripts.linkedin_url_backfill_dryrun_v5 import load_proposals, preview_backfill
from tests.test_linkedin_verified_url_evidence_v5 import job, verified

NOW = datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc)


def build_db(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE jobs(id INTEGER PRIMARY KEY, source TEXT, "
            "company_name TEXT, title TEXT, job_url TEXT, ats_job_id TEXT, apply_url TEXT)"
        )
        sample = job()
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
            tuple(
                sample.get(x)
                for x in (
                    "id",
                    "source",
                    "company_name",
                    "title",
                    "job_url",
                    "ats_job_id",
                    "apply_url",
                )
            ),
        )


def test_no_proposals_zero_mutations(tmp_path):
    path = tmp_path / "jobs.sqlite"
    build_db(path)
    before = path.read_bytes()
    with read_only_connection(path) as db:
        result = preview_backfill(db, now=NOW)
    assert result["linkedin_jobs_inspected"] == 1
    assert result["status_counts"] == {"NO_VERIFIED_EVIDENCE_SUPPLIED": 1}
    assert result["actual_inserts"] == 0
    assert path.read_bytes() == before


def test_valid_proposal_preview_only_no_write(tmp_path):
    path = tmp_path / "jobs.sqlite"
    build_db(path)
    proposal = {
        "job_id": 101,
        "verified_at": NOW.isoformat(),
        "resolution": verified().to_dict(),
    }
    before = path.read_bytes()
    with read_only_connection(path) as db:
        result = preview_backfill(db, (proposal,), now=NOW)
    assert result["status_counts"] == {"WOULD_INSERT_IF_SEPARATELY_AUTHORIZED": 1}
    assert result["actual_inserts"] == 0
    assert path.read_bytes() == before


def test_invalid_proof_not_promoted_to_verified(tmp_path):
    path = tmp_path / "jobs.sqlite"
    build_db(path)
    proof = verified().to_dict()
    proof["resolution_status"] = "CANDIDATE"
    with read_only_connection(path) as db:
        result = preview_backfill(
            db,
            ({"job_id": 101, "verified_at": NOW.isoformat(), "resolution": proof},),
            now=NOW,
        )
    assert result["status_counts"] == {"REJECTED_INVALID_OR_UNVERIFIED_EVIDENCE": 1}


def test_same_proposal_repeated_rejected_by_id(tmp_path):
    path = tmp_path / "jobs.sqlite"
    build_db(path)
    p = {"job_id": 101}
    with (
        read_only_connection(path) as db,
        pytest.raises(ValueError, match="unique positive"),
    ):
        preview_backfill(db, (p, p), now=NOW)


def test_unknown_job_proposal_not_injected(tmp_path):
    path = tmp_path / "jobs.sqlite"
    build_db(path)
    with read_only_connection(path) as db:
        out = preview_backfill(db, ({"job_id": 999},), now=NOW)
    assert out["proposals_without_current_linkedin_job"] == 1
    assert out["actual_inserts"] == 0


def test_bad_proposal_file_never_causes_database_creation(tmp_path):
    proposals = tmp_path / "proposals.json"
    proposals.write_text(json.dumps({"not": "a list"}))
    with pytest.raises(ValueError):
        load_proposals(proposals)
