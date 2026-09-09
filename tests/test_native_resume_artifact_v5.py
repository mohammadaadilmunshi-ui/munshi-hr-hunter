from __future__ import annotations

import hashlib
import io

import pytest
from pypdf import PdfWriter

from app import database
from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5
from tests.test_stage_b_native_resume_v5 import _confirmed_profile, _fake_writer, _job


def _pdf(*, pages: int = 1, suffix: bytes = b"") -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue() + suffix


def _renderers(
    monkeypatch: pytest.MonkeyPatch, *, pages: int = 1, suffix: bytes = b""
) -> None:
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_pdf",
        lambda _version_id: (_pdf(pages=pages, suffix=suffix), pages),
    )
    if pages == 1:
        monkeypatch.setattr(
            artifacts,
            "_validated_pdf_layout",
            lambda _data: {
                "status": "PASS",
                "pdf_pages": 1,
                "occupied_height_ratio": 0.82,
                "bottom_whitespace_ratio": 0.12,
                "text_fragment_count": 40,
            },
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

    assert first["page_verification"]["status"] == "PASS"
    assert first["page_verification"]["pdf_pages"] == 1
    assert first["page_verification"]["occupied_height_ratio"] == 0.82
    assert first["pdf"]["page_count"] == 1
    assert first["pdf"]["sha256"] == hashlib.sha256(_pdf()).hexdigest()
    assert first["docx"]["sha256"] == hashlib.sha256(
        resume_v5.v4.v1.version_docx(resume["version_id"])
    ).hexdigest()
    assert second["pdf"]["artifact_id"] == first["pdf"]["artifact_id"]
    assert second["docx"]["artifact_id"] == first["docx"]["artifact_id"]
    assert artifacts.artifact_bytes(first["pdf"]["artifact_id"]) == _pdf()

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
    # The rejection happens before materialize opens its persistence connection.
    # Create the artifact schema explicitly so this test can assert the intended
    # no-write invariant rather than depending on another test's setup order.
    artifacts.ensure_schema()
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    _renderers(monkeypatch, pages=2)

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


def test_materialize_rejects_a_corrupt_pdf_even_if_renderer_claims_one_page(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    monkeypatch.setattr(
        resume_v5.v4.v1,
        "version_pdf",
        lambda _version_id: (b"%PDF-1.7\nnot a parseable PDF", 1),
    )

    with pytest.raises(RuntimeError, match="invalid document"):
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

def test_page_fit_metrics_rejects_sparse_resume() -> None:
    with pytest.raises(ValueError, match="under-filled"):
        artifacts._page_fit_metrics(
            page_height=792.0,
            fragments=[
                (730.0, 20.0),
                (690.0, 10.0),
                (650.0, 10.0),
                (610.0, 10.0),
                (570.0, 10.0),
                (530.0, 10.0),
            ],
        )


def test_page_fit_metrics_accepts_professional_full_page_coverage() -> None:
    result = artifacts._page_fit_metrics(
        page_height=792.0,
        fragments=[
            (744.0, 21.0),
            (710.0, 10.0),
            (660.0, 10.0),
            (590.0, 10.0),
            (500.0, 10.0),
            (410.0, 10.0),
            (320.0, 10.0),
            (230.0, 10.0),
            (140.0, 10.0),
            (82.0, 10.0),
        ],
    )
    assert result["status"] == "PASS"
    assert result["occupied_height_ratio"] >= artifacts.MIN_OCCUPIED_HEIGHT_RATIO
    assert (
        result["bottom_whitespace_ratio"]
        <= artifacts.MAX_BOTTOM_WHITESPACE_RATIO
    )
