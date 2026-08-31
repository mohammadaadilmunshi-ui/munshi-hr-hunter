from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.render_n8n_deployment_workflow import render

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "n8n/workflows/canonical_hr_hunter_workflow.json"


def test_renderer_creates_copy_without_touching_canonical(tmp_path: Path):
    before = CANONICAL.read_bytes()
    digest = hashlib.sha256(before).hexdigest()
    output = tmp_path / "deployment.json"
    render("https://hunter.test:8000", "http://ollama.test:11434", output)
    generated = output.read_text(encoding="utf-8")
    assert "https://hunter.test:8000/api/hr-agent/score" in generated
    assert "https://hunter.test:8000/api/n8n/status-update" in generated
    assert "http://ollama.test:11434/api/generate" in generated
    assert "http://n8n:5678/webhook/aadil-job-hunter-intake-v05" in generated
    for legacy_url in (
        "http://127.0.0.1:8000/api/hr-agent/score",
        "http://127.0.0.1:8000/api/n8n/status-update",
        "http://127.0.0.1:11434/api/generate",
        "http://localhost:5678",
    ):
        assert legacy_url not in generated

    canonical = before.decode("utf-8")
    for semantic_marker in (
        "localhost_handoff",
        "localhost_callback_payload",
        "localhost_execution_scope",
        "localhost_job_hunter",
        "localhost_callback_required",
        "localhost_metadata",
    ):
        assert semantic_marker in generated
        assert generated.count(semantic_marker) == canonical.count(semantic_marker)

    # This is pin-data metadata, not one of the contract's classified endpoint bases.
    assert '"host": "127.0.0.1:5678"' in generated
    assert CANONICAL.read_bytes() == before
    assert hashlib.sha256(CANONICAL.read_bytes()).hexdigest() == digest


def test_renderer_rejects_canonical_output():
    with pytest.raises(ValueError, match="canonical"):
        render("http://hunter:8000", "http://ollama:11434", CANONICAL)
