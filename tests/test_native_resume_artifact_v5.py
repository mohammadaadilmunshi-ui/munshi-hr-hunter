from __future__ import annotations

import hashlib

import pytest

from app import database
from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5
from tests.test_stage_b_native_resume_v5 import _confirmed_profile, _fake_writer, _job


def _renderers(monkeypatch: pytest.MonkeyPatch, *, suffix: bytes = b"") -> None:
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_docx",
        lambda _version_id: b"PK\x03\x04synthetic-docx" + suffix,
    )
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_pdf",
        lambda _version_id: (b"%PDF-1.7\nsynthetic-one-page" + suffix, 1),
    )


def test_materialize_persists_exact_one_page_pdf_and_docx_idempotently(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()
    resume = resume_v5.generate_resume(job_id=job_id)
    _renderers(monkeypatch)

    first = artifacts.materialize(resume["version_id"])
    second = artifacts.materialize(resume["version_id"])

    assert first["page_verification"] == {"status": "PASS", "pdf_pages": 1}
    assert first["pdf"]["page_count"] == 1
    assert first["pdf"]["sha256"] == hashlib.sha256(
        b"%PDF-1.7\nsynthetic-one-page"
    ).hexdigest()
    assert first["docx"]["sha256"] == hashlib.sha256(
        b"PK\x03\x04synthetic-docx"
    ).hexdigest()
    assert second["pdf"]["artifact_id"] == first["pdf"]["artifact_id"]
    assert second["docx"]["artifact_id"] == first["docx"]["artifact_id"]
    assert artifacts.artifact_bytes(first["pdf"]["artifact_id"]) == (
        b"%PDF-1.7\nsynthetic-one-page"
    )

    connection = database.get_connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM native_resume_v5_artifacts WHERE version_id=?",
            (resume["version_id"],),
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_materialize_rejects_non_one_page_pdf_before_persistence(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_docx",
        lambda _version_id: b"PK\x03\x04synthetic-docx",
    )
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_pdf",
        lambda _version_id: (b"%PDF-1.7\nsynthetic-two-page", 2),
    )

    with pytest.raises(ValueError, match="exactly one page"):
        artifacts.materialize(resume["version_id"])

    connection = database.get_connection()
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM native_resume_v5_artifacts WHERE version_id=?",
            (resume["version_id"],),
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_immutable_version_cannot_silently_change_artifact_bytes(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    _renderers(monkeypatch)
    artifacts.materialize(resume["version_id"])

    _renderers(monkeypatch, suffix=b"-changed")
    with pytest.raises(RuntimeError, match="Immutable Native Resume V5 artifact content changed"):
        artifacts.materialize(resume["version_id"])


def test_artifact_materialization_fails_closed_after_job_snapshot_changes(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    job_id = _job()
    resume = resume_v5.generate_resume(job_id=job_id)
    _renderers(monkeypatch)

    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET title='Changed after resume generation' WHERE id=?",
            (job_id,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="stored job changed"):
        artifacts.materialize(resume["version_id"])
