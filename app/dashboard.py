from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# Establish viewport geometry before loading the larger product modules.
st.set_page_config(
    page_title="MUNSHI Apply | Job Intelligence",
    page_icon=str(Path(__file__).resolve().parent / "assets" / "munshi_crown_favicon.png"),
    layout="wide",
    initial_sidebar_state="collapsed",
)

from app.database import get_connection, initialize_database  # noqa: E402
from app.initial_dashboard_runtime_hotfix_v1 import install as install_runtime_hotfix  # noqa: E402
from app.product_shell import render  # noqa: E402


_REQUIRED_STREAMLIT_TABLES = frozenset(
    {
        "settings",
        "jobs",
        "product_job_state",
        "tenant_memberships",
        "native_resume_sources",
        "native_resume_versions",
        "application_answer_vault",
    }
)


def _dashboard_runtime_schema_ready() -> bool:
    """Read-only readiness probe; ordinary Streamlit reruns must not run DDL."""
    connection = get_connection()
    try:
        placeholders = ",".join("?" for _ in _REQUIRED_STREAMLIT_TABLES)
        rows = connection.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
            tuple(_REQUIRED_STREAMLIT_TABLES),
        ).fetchall()
        present = {str(row[0]) for row in rows}
        return _REQUIRED_STREAMLIT_TABLES.issubset(present)
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _ensure_dashboard_runtime_ready() -> None:
    """Compatibility fallback for standalone development launches only.

    In the supervised staging/production runtime FastAPI is started and proven
    healthy before Streamlit starts, so this branch should not execute there.
    """
    if _dashboard_runtime_schema_ready():
        return

    initialize_database()
    from app.native_resume_service import ensure_schema as ensure_native_resume_studio_schema

    ensure_native_resume_studio_schema()
    if not _dashboard_runtime_schema_ready():
        raise RuntimeError(
            "MUNSHI dashboard schema is not ready after startup initialization."
        )


_ensure_dashboard_runtime_ready()
install_runtime_hotfix()
render()
