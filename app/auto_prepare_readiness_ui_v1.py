"""Auto Prepare readiness banner for canonical runtime prerequisites."""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.database import get_setting


_REQUIRED_SCORING = (
    ("auto_n8n_threshold", float, "Automatic admission threshold"),
    ("daily_auto_n8n_limit", int, "Automatic daily limit"),
    ("daily_manual_n8n_limit", int, "Manual preparation daily limit"),
)


def readiness_snapshot() -> dict[str, Any]:
    scoring = dict(get_setting("scoring", {}) or {})
    values: dict[str, Any] = {}
    missing: list[str] = []

    for key, value_type, _ in _REQUIRED_SCORING:
        try:
            value = value_type(scoring[key])
        except (KeyError, TypeError, ValueError):
            missing.append(key)
            continue
        values[key] = value

    return {
        "ready": not missing,
        "missing": missing,
        "values": values,
    }


def render_auto_prepare_readiness() -> None:
    snapshot = readiness_snapshot()
    with st.container(border=True):
        st.markdown("### Automation readiness")
        if snapshot["ready"]:
            st.success(
                "Canonical scoring prerequisites are configured. Eligible jobs "
                "can enter automatic preparation subject to targeting, dedupe and "
                "the existing safety gates."
            )
            values = snapshot["values"]
            cols = st.columns(3, gap="small")
            cols[0].metric("Auto threshold", f"{values['auto_n8n_threshold']:g}")
            cols[1].metric("Auto daily limit", values["daily_auto_n8n_limit"])
            cols[2].metric("Manual daily limit", values["daily_manual_n8n_limit"])
        else:
            missing = ", ".join(snapshot["missing"])
            st.error(
                "Automatic preparation is currently blocked by incomplete "
                f"canonical scoring settings: {missing}."
            )
            st.caption(
                "This is the same fail-closed gate used by the dispatcher. The "
                "dashboard now surfaces it before a job silently fails to enter "
                "OPTIMIZING."
            )


def install_auto_prepare_readiness_ui(pages_module: Any) -> None:
    if getattr(pages_module, "_auto_prepare_readiness_ui_v1_installed", False):
        return
    original_auto_prepare = pages_module.auto_prepare

    def auto_prepare_with_readiness() -> None:
        render_auto_prepare_readiness()
        original_auto_prepare()

    pages_module.auto_prepare = auto_prepare_with_readiness
    pages_module._auto_prepare_readiness_ui_v1_installed = True
