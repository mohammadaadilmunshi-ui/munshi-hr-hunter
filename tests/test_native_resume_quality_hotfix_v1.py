from __future__ import annotations

import json

import pytest

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_quality_hotfix_v1 as quality
from app import native_resume_service_v2 as v2
from app import resume_one_page_guard_v1 as one_page


def _document() -> dict:
    return {
        "schema_version": "native-resume-v1",
        "template_version": "ats-single-column-v1",
        "candidate_name": "Aadil Munshi",
        "contact": {
            "location": "New Jersey",
            "email": "aadil@example.com",
            "phone": "",
            "linkedin": "",
            "github": "",
            "portfolio": "",
        },
        "summary": {
            "text": "People analytics candidate with evidence-backed recruiting and operations experience.",
            "evidence_ids": ["e1"],
        },
        "education": [],
        "skills": [
            {"label": "Analytics", "skills": ["Excel"], "evidence_ids": ["e1"]}
        ],
        "experience": [
            {
                "organization": "Example Co",
                "title": "HR Intern",
                "dates": "2024",
                "location": "",
                "bullets": [
                    {
                        "text": "Supported recruiting operations and documented candidate workflows accurately.",
                        "evidence_ids": ["e1"],
                    }
                ],
            }
        ],
        "projects": [],
        "certifications": [],
    }


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Client:
    payload: dict = {}
    posted_json: dict | None = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, _url, *, headers, json):
        type(self).posted_json = json
        return _Response(type(self).payload)


def _config(max_output_tokens: int = 6000) -> dict:
    return {
        "api_key": "sk-test-not-real",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "max_output_tokens": max_output_tokens,
        "max_calls_per_generation": 2,
        "key_source": "test",
    }


def test_structured_transport_restores_proven_output_budget_and_parses_complete_object(monkeypatch):
    doc = _document()
    _Client.payload = {
        "id": "resp_123",
        "status": "completed",
        # Tolerate a harmless wrapper without attempting to invent JSON tokens.
        "output_text": "Here is the resume:\n" + json.dumps(doc) + "\nDone.",
    }
    _Client.posted_json = None
    monkeypatch.setattr(quality.httpx, "Client", _Client)

    payload, response_id, model = quality._reliable_call_openai_v2(
        prompt_payload={"task": "generate_resume"},
        config=_config(6000),
        rewrite_mode="medium",
    )

    assert payload == doc
    assert response_id == "resp_123"
    assert model == "gpt-5.6-terra"
    assert _Client.posted_json is not None
    assert _Client.posted_json["max_output_tokens"] == quality.MIN_STRUCTURED_OUTPUT_TOKENS
    assert _Client.posted_json["text"]["format"]["type"] == "json_schema"


def test_structured_transport_preserves_higher_user_cap(monkeypatch):
    doc = _document()
    _Client.payload = {"id": "resp_456", "status": "completed", "output_text": json.dumps(doc)}
    _Client.posted_json = None
    monkeypatch.setattr(quality.httpx, "Client", _Client)

    quality._reliable_call_openai_v2(
        prompt_payload={"task": "generate_resume"},
        config=_config(12000),
        rewrite_mode="medium",
    )
    assert _Client.posted_json["max_output_tokens"] == 12000


def test_incomplete_structured_response_fails_with_specific_truncation_error(monkeypatch):
    _Client.payload = {
        "id": "resp_cut",
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output_text": '{"schema_version":"native-resume-v1"',
    }
    monkeypatch.setattr(quality.httpx, "Client", _Client)

    with pytest.raises(RuntimeError, match="truncated"):
        quality._reliable_call_openai_v2(
            prompt_payload={"task": "generate_resume"},
            config=_config(),
            rewrite_mode="medium",
        )


def test_schema_invalid_json_object_fails_before_resume_persistence(monkeypatch):
    _Client.payload = {
        "id": "resp_bad_schema",
        "status": "completed",
        "output_text": json.dumps({"hello": "world"}),
    }
    monkeypatch.setattr(quality.httpx, "Client", _Client)
    with pytest.raises(RuntimeError, match="did not match"):
        quality._reliable_call_openai_v2(
            prompt_payload={"task": "generate_resume"},
            config=_config(),
            rewrite_mode="medium",
        )


def test_underfill_repair_expands_only_supported_evidence():
    text = quality._adaptive_repair_instruction(
        issues=[], renderer_error="Native Resume V5 PDF is visibly under-filled; regenerate with more supported evidence."
    )
    lowered = text.casefold()
    assert "fuller revision" in lowered
    assert "supported evidence" in lowered
    assert "do not invent" in lowered
    assert "tighter revision" not in lowered


def test_overflow_repair_compresses_without_shrinking_readability():
    text = quality._adaptive_repair_instruction(
        issues=["document_over_650_safe_words"], renderer_error="parsed 2 pages"
    )
    lowered = text.casefold()
    assert "tighter revision" in lowered
    assert "lowest-value" in lowered
    assert "do not add new claims" in lowered
    assert "shrink readability" in lowered


def test_install_tightens_professional_page_fit_and_patches_v2_idempotently():
    original_call = v2._call_openai_v2
    original_direction = one_page._ONE_PAGE_DIRECTION
    original_words = one_page.MAX_SAFE_WORDS
    original_bullet = one_page.MAX_SAFE_BULLET_WORDS
    original_lines = one_page.MAX_SAFE_ESTIMATED_LINES
    original_repair = one_page._repair_instruction
    original_occupied = artifacts.MIN_OCCUPIED_HEIGHT_RATIO
    original_blank = artifacts.MAX_BOTTOM_WHITESPACE_RATIO
    try:
        first = quality.install()
        second = quality.install()
        assert first["success"] is True
        assert second["success"] is True
        assert getattr(v2._call_openai_v2, "_aadil_native_resume_reliable_json", False) is True
        assert artifacts.MIN_OCCUPIED_HEIGHT_RATIO == pytest.approx(0.70)
        assert artifacts.MAX_BOTTOM_WHITESPACE_RATIO == pytest.approx(0.24)
        assert one_page.MAX_SAFE_WORDS == 650
        assert one_page.MAX_SAFE_BULLET_WORDS == 28
        assert one_page.MAX_SAFE_ESTIMATED_LINES == 70
        assert first["submission_authority"] is False
    finally:
        v2._call_openai_v2 = original_call
        one_page._ONE_PAGE_DIRECTION = original_direction
        one_page.MAX_SAFE_WORDS = original_words
        one_page.MAX_SAFE_BULLET_WORDS = original_bullet
        one_page.MAX_SAFE_ESTIMATED_LINES = original_lines
        one_page._repair_instruction = original_repair
        artifacts.MIN_OCCUPIED_HEIGHT_RATIO = original_occupied
        artifacts.MAX_BOTTOM_WHITESPACE_RATIO = original_blank
