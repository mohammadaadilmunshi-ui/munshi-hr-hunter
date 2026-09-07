from __future__ import annotations

import hashlib
import io

import pytest
from pypdf import PdfWriter

from app import application_execution_artifact as execution_artifact
from app import native_resume_service_v5 as resume_v5
from tests.test_native_resume_artifact_v5 import _renderers
from tests.test_stage_b_native_resume_v5 import _confirmed_profile, _fake_writer, _job


def test_native_artifact_normalizes_immutable_pdf(hunter_db, monkeypatch) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    _renderers(monkeypatch)

    artifact = execution_artifact.native(application_id="application-1", version_id=resume["version_id"])

    assert artifact["engine"] == "native"
    assert artifact["version_id"] == resume["version_id"]
    assert artifact["page_count"] == 1
    assert artifact["submission_authority"] is False


def test_n8n_artifact_keeps_n8n_provenance_and_rejects_wrong_job() -> None:
    payload = {
        "engine": "n8n", "job_id": 41, "artifact_id": "n8n-artifact-1",
        "version_id": "n8n-version-1", "version_number": 4, "filename": "resume.pdf",
        "mime_type": "application/pdf", "artifact_reference": "hunter-n8n://artifact/1",
        "artifact_sha256": "a" * 64, "page_count": 1,
        "candidate_truth_binding": {"profile_digest": "b" * 64},
        "job_snapshot_binding": {"job_snapshot_sha256": "c" * 64},
        "created_at": "2026-09-07T00:00:00+00:00",
    }
    artifact = execution_artifact.n8n(application_id="application-1", job_id=41, callback=payload)
    assert artifact["engine"] == "n8n"
    with pytest.raises(ValueError, match="another job"):
        execution_artifact.n8n(application_id="application-1", job_id=42, callback=payload)
