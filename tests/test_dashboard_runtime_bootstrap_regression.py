from __future__ import annotations

from pathlib import Path


def test_dashboard_database_bootstrap_is_cached_per_streamlit_process() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "dashboard.py"
    ).read_text(encoding="utf-8")

    assert "@st.cache_resource(show_spinner=False)" in source
    assert "def _initialize_dashboard_runtime() -> bool:" in source

    # Heavy initialization remains present exactly once, but now lives behind
    # Streamlit's process-level resource cache rather than top-level rerun code.
    assert source.count("initialize_database()") == 1
    assert source.count("ensure_native_resume_studio_schema()") == 1

    definition = source.index("def _initialize_dashboard_runtime() -> bool:")
    invocation = source.rindex("_initialize_dashboard_runtime()")
    render = source.index("render()")

    assert definition < invocation < render
