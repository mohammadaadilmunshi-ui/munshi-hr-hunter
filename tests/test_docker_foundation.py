from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.validate_docker_foundation import CANONICAL_SHA256, validate


ROOT = Path(__file__).resolve().parents[1]


def test_docker_foundation_is_valid() -> None:
    assert validate(ROOT) == []


def test_canonical_workflow_is_unchanged() -> None:
    path = ROOT / "n8n/workflows/canonical_hr_hunter_workflow.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == CANONICAL_SHA256


def test_compose_uses_internal_endpoints_and_named_state() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "FASTAPI_BASE_URL: http://hunter:8000" in compose
    assert "OLLAMA_BASE_URL: http://ollama:11434" in compose
    assert "hunter_data:/app/hunter/data" in compose
    assert "n8n_data:/home/node/.n8n" in compose
    assert "N8N_USER_FOLDER: /home/node" in compose
    assert "N8N_USER_FOLDER: /home/node/.n8n" not in compose
    assert "HUNTER_FASTAPI_PORT_MAPPING:-127.0.0.1:8000:8000" in compose
    assert "N8N_PORT_MAPPING:-127.0.0.1:5678:5678" in compose
