"""Profile runtime repair V2.

Installs the hardened deterministic parser after the existing Career OS quality
layer and exposes an explicit rebuild action for the current Master Resume.
Rebuild creates a new reviewable draft only; it never confirms or replaces
permanent profile authority automatically.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.deterministic_profile_extractor_v2_1 import extract_profile_from_source


def install_profile_runtime_repair_v2() -> None:
    from app import native_resume_service_v3 as service
    from app import profile_workspace_v3 as workspace

    if getattr(workspace, "_profile_runtime_repair_v2_installed", False):
        return

    # Career OS V1 intentionally replaced the model-backed extractor with a
    # deterministic parser.  V2.1 keeps that same no-model contract while
    # adding layout repair for wrapped PDF/DOCX text and flattened project
    # boundaries.
    service.extract_profile_from_source = extract_profile_from_source

    original_controls = workspace._profile_controls
    workspace._profile_runtime_repair_v2_original_controls = original_controls

    def controls_with_rebuild(extracted: dict[str, Any], resolved: dict[str, Any]) -> None:
        source = service.active_source()
        if source:
            with st.container(border=True):
                left, right = st.columns((1.2, 2.8), gap="small")
                with left:
                    if st.button(
                        "Rebuild preview from current Master Resume",
                        type="secondary",
                        use_container_width=True,
                        key=f"profile_runtime_v2_rebuild_{source['source_id']}",
                    ):
                        try:
                            extract_profile_from_source(source)
                        except Exception as error:
                            st.error(str(error))
                        else:
                            st.session_state["profile_v3_edit_open"] = False
                            st.success(
                                "New deterministic profile preview created from the current Master Resume. "
                                "Review it before confirming it as permanent."
                            )
                            st.rerun()
                with right:
                    model = str(extracted.get("model") or "stored extraction")
                    status = str(extracted.get("status") or "draft").upper()
                    st.caption(
                        f"Current profile extraction: {model} · {status}. "
                        "Rebuild creates a new draft only; it never confirms or deletes the current permanent profile."
                    )

        original_controls(extracted, resolved)

    workspace._profile_controls = controls_with_rebuild
    workspace._profile_runtime_repair_v2_installed = True
