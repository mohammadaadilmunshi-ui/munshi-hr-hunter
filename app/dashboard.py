from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import initialize_database  # noqa: E402
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
initialize_database()
render()
