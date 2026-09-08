from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import initialize_database  # noqa: E402
from app.native_resume_service import ensure_schema as ensure_native_resume_studio_schema  # noqa: E402
from app.product_shell import render  # noqa: E402


st.set_page_config(
    page_title="MUNSHI Apply | Job Intelligence",
    page_icon=str(
        Path(__file__).resolve().parent
        / "assets"
        / "munshi_crown_favicon.png"
    ),
    layout="wide",
    initial_sidebar_state="collapsed",
)
@st.cache_resource(show_spinner=False)
def _initialize_dashboard_runtime() -> bool:
    """Initialize additive database/runtime schemas once per Streamlit process.

    Streamlit reruns this module for UI interaction. Re-running the complete
    database initializer on every rerun turns ordinary dashboard reads into
    SQLite writers and can contend with schedulers, callbacks, and Telegram.
    """
    initialize_database()
    ensure_native_resume_studio_schema()
    return True


_initialize_dashboard_runtime()
render()
