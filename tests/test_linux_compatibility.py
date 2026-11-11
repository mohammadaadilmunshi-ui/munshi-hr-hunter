import json
from pathlib import Path

from app import platform_config


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_contract_separates_source_and_linux_proof_target():
    contract = json.loads((ROOT / "config/runtime_versions.json").read_text(encoding="utf-8"))
    assert contract["production_source_environment"] == {
        "python": "3.12.14",
        "node": "24.16.0",
        "npm": "11.13.0",
        "n8n": "2.22.5",
        "architecture": "arm64",
    }
    assert contract["linux_ci_proof_target"] == {
        "python": "3.12",
        "architecture": "x86_64",
        "arm64_linux_status": "unproven",
    }


def test_portable_configuration_has_no_personal_path(monkeypatch, tmp_path):
    monkeypatch.setattr(platform_config.platform, "system", lambda: "Linux")
    monkeypatch.setenv("AADIL_HR_HUNTER_PROJECT", str(tmp_path))
    assert str(platform_config.project_root()) == str(tmp_path)
    assert "/Users/aadil" not in str(platform_config.project_root())


def test_configured_service_endpoint_is_used_in_portable_mode(monkeypatch):
    monkeypatch.setattr(platform_config.platform, "system", lambda: "Linux")
    monkeypatch.setenv("FASTAPI_BASE_URL", "http://api.service:9000")
    monkeypatch.setenv("FASTAPI_HOST", "api.service")
    monkeypatch.setenv("FASTAPI_PORT", "9000")
    assert platform_config.endpoint_url("fastapi", "/health") == "http://api.service:9000/health"
