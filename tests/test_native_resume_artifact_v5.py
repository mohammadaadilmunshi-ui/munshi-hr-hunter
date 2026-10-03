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
        lambda _version_id, **_kwargs: (_pdf(pages=pages, suffix=suffix), pages),
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



def test_materialize_uses_compact_local_render_before_failing(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())

    calls: list[str] = []

    def render(_version_id: str, *, render_density: str = "normal"):
        calls.append(render_density)
        pages = 2 if render_density == "normal" else 1
        return _pdf(pages=pages), pages

    monkeypatch.setattr(resume_v5.v4.v1, "version_pdf", render)
    monkeypatch.setattr(
        artifacts,
        "_validated_pdf_layout",
        lambda _data: {
            "status": "PASS",
            "pdf_pages": 1,
            "occupied_height_ratio": 0.80,
            "bottom_whitespace_ratio": 0.14,
            "text_fragment_count": 38,
        },
    )

    result = artifacts.materialize(resume["version_id"])

    assert calls[:2] == ["normal", "compact"]
    assert result["page_verification"]["render_density"] == "compact"
    assert result["render_density"] == "compact"
    assert result["pdf"]["page_count"] == 1

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


def test_materialize_reuses_persisted_immutable_pair_without_rerender(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    _renderers(monkeypatch)
    first = artifacts.materialize(resume["version_id"])

    def must_not_render(*args, **kwargs):
        raise AssertionError("persisted immutable resume must not be re-rendered")

    monkeypatch.setattr(resume_v5.v4.v1, "version_pdf", must_not_render)
    monkeypatch.setattr(resume_v5.v4.v1, "version_docx", must_not_render)

    second = artifacts.materialize(resume["version_id"])

    assert second["pdf"]["artifact_id"] == first["pdf"]["artifact_id"]
    assert second["docx"]["artifact_id"] == first["docx"]["artifact_id"]
    assert second["pdf"]["sha256"] == first["pdf"]["sha256"]
    assert second["docx"]["sha256"] == first["docx"]["sha256"]
    assert second["render_density"] == "stored_immutable"


def test_materialize_reuses_stored_pair_for_proven_inflight_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = {"version_id": "resume-v1", "job_id": 42}
    stored = {
        "pdf": {"artifact_id": "pdf-1"},
        "docx": {"artifact_id": "docx-1"},
    }
    monkeypatch.setattr(
        artifacts,
        "_current_resume",
        lambda _version_id: (_ for _ in ()).throw(
            RuntimeError("Candidate Truth changed after this resume was generated.")
        ),
    )
    monkeypatch.setattr(resume_v5, "get_version", lambda _version_id: record)
    monkeypatch.setattr(
        "app.application_preflight_package_v1.reusable_inflight_preflight",
        lambda **_kwargs: {"package_id": "preflight-1"},
    )
    monkeypatch.setattr(
        artifacts,
        "_stored_materialization",
        lambda **_kwargs: stored,
    )

    result = artifacts.materialize(
        "resume-v1",
        application_id="application-1",
    )

    assert result["pdf"]["artifact_id"] == "pdf-1"
    assert result["immutable_inflight_continuation"] is True
    assert result["preflight_package_id"] == "preflight-1"


def test_materialize_does_not_relax_staleness_without_application_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        artifacts,
        "_current_resume",
        lambda _version_id: (_ for _ in ()).throw(
            RuntimeError("Candidate Truth changed after this resume was generated.")
        ),
    )

    with pytest.raises(RuntimeError, match="Candidate Truth changed"):
        artifacts.materialize("resume-v1")


def test_materialize_fails_closed_if_persisted_artifact_bytes_are_corrupt(
    hunter_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    _confirmed_profile(hunter_db, monkeypatch)
    _fake_writer(monkeypatch)
    resume = resume_v5.generate_resume(job_id=_job())
    _renderers(monkeypatch)
    first = artifacts.materialize(resume["version_id"])

    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE native_resume_v5_artifacts SET content=? WHERE artifact_id=?",
            (b"%PDF-1.7\ncorrupt", first["pdf"]["artifact_id"]),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(RuntimeError, match="stored artifact failed integrity"):
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
        lambda _version_id, **_kwargs: (b"%PDF-1.7\nnot a parseable PDF", 1),
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

def test_effective_pdf_text_geometry_applies_chromium_page_transform() -> None:
    # Chromium emits CSS-space text positions and maps them into PDF points via
    # a 0.75 scale plus a vertical flip/translation.
    y, font = artifacts._effective_pdf_text_geometry(
        cm=[0.75, 0.0, 0.0, -0.75, 39.75, 763.5],
        tm=[1.0, 0.0, 0.0, -1.0, 0.0, 946.0],
        font_size=12.53,
    )
    assert y == pytest.approx(54.0)
    assert font == pytest.approx(9.3975)


def test_effective_pdf_geometry_prevents_false_bottom_edge_failure() -> None:
    cm = [0.75, 0.0, 0.0, -0.75, 39.75, 763.5]
    css_fragments = [
        (24.0, 28.66),
        (87.0, 14.4),
        (220.0, 14.53),
        (430.0, 14.53),
        (700.0, 12.53),
        (946.0, 12.53),
    ]
    fragments = [
        artifacts._effective_pdf_text_geometry(
            cm=cm,
            tm=[1.0, 0.0, 0.0, -1.0, 0.0, css_y],
            font_size=font,
        )
        for css_y, font in css_fragments
    ]

    result = artifacts._page_fit_metrics(
        page_height=792.0,
        fragments=fragments,
    )

    assert result["status"] == "PASS"
    assert result["bottom_whitespace_ratio"] == pytest.approx(54.0 / 792.0, abs=0.001)


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
