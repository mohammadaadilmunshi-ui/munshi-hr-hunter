from __future__ import annotations

import hashlib

import pytest

from app import cover_letter_artifact_v1 as cover

PDF = b"%PDF-1.4\n% cover fixture\n%%EOF\n"
PDF_SHA = hashlib.sha256(PDF).hexdigest()


def package():
    return {
        "package_id": "preflight-cover-1",
        "package_digest": "1" * 64,
        "status": "READY_TO_APPLY",
        "application_id": "application-cover-1",
        "job_id": 42,
        "job_snapshot_sha256": "2" * 64,
        "resume_version_id": "resume-v5-cover-1",
        "snapshot": {
            "job": {"id": 42, "company_name": "Example Co", "title": "People Analyst"},
            "application_truth": {
                "tenant_id": "default",
                "user_id": "local-owner",
                "candidate_profile_binding": {"profile_digest": "3" * 64},
            },
        },
    }


@pytest.fixture
def wired(hunter_db, monkeypatch):
    monkeypatch.setattr(
        cover.preflight_v1, "get_preflight_package", lambda _id: package()
    )
    monkeypatch.setattr(
        cover.preflight_v1, "preflight_package_freshness", lambda _id: {"fresh": True}
    )
    monkeypatch.setattr(
        cover,
        "safe_owned_job_snapshot",
        lambda _id: {
            "job_snapshot_sha256": "2" * 64,
            "job": package()["snapshot"]["job"],
        },
    )
    monkeypatch.setattr(
        cover.application_execution_artifact,
        "native",
        lambda **_k: {
            "artifact_id": "resume-artifact-cover-1",
            "artifact_sha256": "4" * 64,
            "artifact_reference": "hunter-native-resume://cover/pdf",
        },
    )
    monkeypatch.setattr(cover, "_render_pdf", lambda _text, **_k: (PDF, 1))


def test_immutable_owner_bound_idempotent(wired):
    a = cover.create(
        preflight_package_id="preflight-cover-1",
        content_text="Dear Hiring Team,\n\nI am interested in this role.",
    )
    b = cover.create(
        preflight_package_id="preflight-cover-1",
        content_text="Dear Hiring Team,\n\nI am interested in this role.",
    )
    assert a["cover_letter_id"] == b["cover_letter_id"]
    assert a["artifact_sha256"] == PDF_SHA
    assert a["page_count"] == 1
    assert a["resume_artifact_sha256"] == "4" * 64
    assert a["candidate_profile_digest"] == "3" * 64
    assert a["submission_authority"] is False
    assert cover.artifact_bytes(a["cover_letter_id"]) == PDF
    assert cover.freshness(a["cover_letter_id"])["fresh"] is True


def test_fails_closed_when_preflight_stale(wired, monkeypatch):
    a = cover.create(
        preflight_package_id="preflight-cover-1",
        content_text="Dear Hiring Team,\n\nPrepared truthfully.",
    )
    monkeypatch.setattr(
        cover.preflight_v1, "preflight_package_freshness", lambda _id: {"fresh": False}
    )
    r = cover.freshness(a["cover_letter_id"])
    assert r["fresh"] is False
    assert r["submission_authority"] is False


def test_content_budget():
    with pytest.raises(ValueError, match="at most"):
        cover._content("x" * (cover.MAX_CONTENT_CHARS + 1))


def test_idempotent_replay_reuses_first_rendered_bytes(wired, monkeypatch):
    alternate = b"%PDF-1.4\n% intentionally different second render\n%%EOF\n"
    renders = [(PDF, 1), (alternate, 1)]

    def render(*_args, **_kwargs):
        return renders.pop(0)

    monkeypatch.setattr(cover, "_render_pdf", render)
    first = cover.create(
        preflight_package_id="preflight-cover-1",
        content_text="Dear Hiring Team,\n\nStable logical replay.",
    )
    second = cover.create(
        preflight_package_id="preflight-cover-1",
        content_text="Dear Hiring Team,\n\nStable logical replay.",
    )
    assert second["cover_letter_id"] == first["cover_letter_id"]
    assert second["artifact_sha256"] == first["artifact_sha256"]
    assert cover.artifact_bytes(first["cover_letter_id"]) == PDF
    assert len(renders) == 1
