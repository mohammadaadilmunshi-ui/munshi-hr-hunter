from pathlib import Path

from app import platform_config


def test_relative_defaults_are_rooted_in_project(monkeypatch):
    monkeypatch.delenv("AADIL_HR_HUNTER_PROJECT", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    assert platform_config.database_path() == platform_config.project_root() / "data/hunter.db"


def test_environment_overrides_and_endpoint_construction(monkeypatch, tmp_path):
    monkeypatch.setenv("AADIL_HR_HUNTER_PROJECT", str(tmp_path))
    monkeypatch.setenv("DATABASE_PATH", "state/hunter.sqlite")
    monkeypatch.setenv("FASTAPI_HOST", "api.internal")
    monkeypatch.setenv("FASTAPI_PORT", "9100")
    monkeypatch.setenv("N8N_BASE_URL", "http://n8n.internal:5678/")
    assert platform_config.database_path() == tmp_path / "state/hunter.sqlite"
    assert platform_config.fastapi_endpoint().base_url == "http://api.internal:9100"
    assert platform_config.endpoint_url("n8n", "/healthz") == "http://n8n.internal:5678/healthz"


def test_ollama_is_optional_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_ENABLED", raising=False)
    monkeypatch.delenv("OLLAMA_REQUIRED", raising=False)
    assert platform_config.ollama_enabled() is False
    assert platform_config.ollama_required() is False


def test_ollama_required_implies_enabled(monkeypatch):
    monkeypatch.setenv("OLLAMA_REQUIRED", "true")
    assert platform_config.ollama_enabled() is True
    assert platform_config.ollama_required() is True


def test_portable_platform_uses_external_scheduler(monkeypatch):
    monkeypatch.setattr(platform_config.platform, "system", lambda: "Linux")
    monkeypatch.delenv("AADIL_HR_HUNTER_PLATFORM", raising=False)
    monkeypatch.delenv("SCHEDULER_BACKEND", raising=False)
    assert platform_config.platform_mode() == "portable"
    assert platform_config.scheduler_backend() == "external"


def test_launch_agents_are_rejected_off_macos(monkeypatch):
    monkeypatch.setattr(platform_config.platform, "system", lambda: "Linux")
    try:
        from app.runtime_config import launch_agent_plist
        launch_agent_plist("example")
    except RuntimeError as exc:
        assert "macOS" in str(exc)
    else:
        raise AssertionError("LaunchAgent resolution must not be used on Linux")
