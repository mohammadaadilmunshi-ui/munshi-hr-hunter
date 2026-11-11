from __future__ import annotations

import ast
from pathlib import Path

from app import autoapply_settings_ui
from app import product_state
from app import product_v22
from app import tenant_foundation
from app.initial_dashboard_runtime_hotfix_v1 import install
from app.provider_telemetry_window_v1 import _WINDOW_OPTIONS


ROOT = Path(__file__).resolve().parent.parent


def _top_level_call_names(source: str) -> set[str]:
    tree = ast.parse(source)
    result: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        target = node.value.func
        if isinstance(target, ast.Name):
            result.add(target.id)
        elif isinstance(target, ast.Attribute):
            result.add(target.attr)
    return result


def test_dashboard_render_no_longer_runs_schema_initializers_unconditionally() -> None:
    source = (ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")
    top_calls = _top_level_call_names(source)

    assert "initialize_database" not in top_calls
    assert "ensure_native_resume_studio_schema" not in top_calls
    assert "_ensure_dashboard_runtime_ready" in top_calls
    assert "install_runtime_hotfix" in top_calls
    assert "render" in top_calls
    assert "sqlite_master" in source


def test_runtime_hotfix_installs_every_declared_guard() -> None:
    result = install()

    assert result["success"] is True
    assert result["dynamic_advanced_renderer"] is True
    assert result["schema_guards"]
    assert all(result["schema_guards"].values())


def test_ready_product_and_tenant_schema_checks_are_read_only(hunter_db) -> None:
    install()

    statements: list[str] = []
    connection = product_state.get_connection()
    connection.set_trace_callback(statements.append)
    try:
        product_state.ensure_schema(connection)
        tenant_foundation.ensure_schema(connection)
    finally:
        connection.set_trace_callback(None)
        connection.close()

    normalized = [statement.strip().upper() for statement in statements]
    assert normalized
    assert not any(statement.startswith("CREATE ") for statement in normalized)
    assert not any(statement.startswith("INSERT ") for statement in normalized)
    assert not any(statement.startswith("UPDATE ") for statement in normalized)
    assert not any(statement.startswith("DELETE ") for statement in normalized)


def test_autoapply_settings_advanced_renderer_tracks_latest_runtime_wrapper(monkeypatch) -> None:
    install()
    called: list[str] = []

    def latest_advanced() -> None:
        called.append("latest")

    monkeypatch.setattr(product_v22, "advanced_v22", latest_advanced)
    autoapply_settings_ui.advanced_v22()

    assert called == ["latest"]


def test_provider_telemetry_window_preserves_expected_time_choices() -> None:
    assert _WINDOW_OPTIONS == {
        "Past 1 hour": 1,
        "Past 6 hours": 6,
        "Past 12 hours": 12,
        "Past 24 hours": 24,
        "Past 3 days": 72,
        "Past 7 days": 168,
        "Past 30 days": 720,
        "All time": 0,
    }
