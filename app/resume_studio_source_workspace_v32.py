"""Resume Studio V3.2 source-workspace state hotfix.

Streamlit forbids mutating a widget-bound session-state key after that widget has
been instantiated during the current render pass.  The V3.1 save path persisted
the Master Resume correctly and then tried to rewrite the text-area and checkbox
keys before calling ``st.rerun()``, which raised ``StreamlitAPIException``.

This module keeps the existing V3.1 evidence/storage authority and changes only
the source-workspace UI state transition: a successful save marks a non-widget
pending flag, then the next rerun applies the saved source to widget keys before
those widgets are instantiated.
"""
from __future__ import annotations

import hashlib
from collections.abc import MutableMapping
from typing import Any

import streamlit as st

from app import resume_studio_page_v3 as v3


_PENDING_SOURCE_REFRESH_KEY = "native_resume_v32_pending_saved_source_refresh"


def mark_source_widget_refresh_pending(session_state: MutableMapping[str, Any]) -> None:
    """Request a safe widget-state refresh on the next Streamlit render pass."""
    session_state[_PENDING_SOURCE_REFRESH_KEY] = True


def apply_pending_source_widget_state(
    session_state: MutableMapping[str, Any],
    source: dict[str, Any] | None,
) -> bool:
    """Apply saved-source state before source widgets are instantiated.

    Returns True when a pending refresh was consumed.  This helper is pure with
    respect to Streamlit itself so the ordering contract can be regression-tested.
    """
    if not bool(session_state.pop(_PENDING_SOURCE_REFRESH_KEY, False)):
        return False

    source = source or {}
    session_state["native_resume_v31_source_draft"] = str(source.get("content_text") or "")
    session_state["native_resume_v31_source_label"] = str(source.get("label") or "Master Resume")
    session_state["native_resume_v31_source_kind"] = str(source.get("source_kind") or "pasted_text")
    session_state["native_resume_v31_truth_confirm"] = False
    return True


def source_workspace() -> dict[str, Any]:
    """Render the V3.1 Master Resume workspace with safe post-save state handling."""
    source = v3.active_source()

    # Critical ordering contract: consume any post-save refresh before creating
    # file/text/checkbox widgets that own the session-state keys below.
    apply_pending_source_widget_state(st.session_state, source)

    st.markdown("### 1. Master resume")
    st.caption(
        "Upload the truthful Master Resume that MUNSHI may use as its primary writing evidence. "
        "PDF, DOCX, TXT, and Markdown are parsed inside the Hunter runtime before you review and confirm the extracted text."
    )

    uploaded = st.file_uploader(
        "Upload master resume",
        type=["pdf", "docx", "txt", "md"],
        help=(
            "PDF, DOCX, TXT, and Markdown are accepted. PDF extraction now uses coordinate-aware layout reconstruction "
            "plus a fragmentation repair pass. Image-only/scanned PDFs still require OCR in a later pass."
        ),
        key="native_resume_v31_source_upload",
    )
    if uploaded is not None:
        raw = uploaded.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if st.session_state.get("native_resume_v31_last_upload_digest") != digest:
            try:
                extracted, source_kind = v3.extract_uploaded_source(uploaded.name, raw)
            except ValueError as error:
                st.error(str(error))
            else:
                # These widget-bound keys are still safe here because their
                # widgets have not yet been instantiated in this render pass.
                st.session_state["native_resume_v31_source_draft"] = extracted
                st.session_state["native_resume_v31_source_kind"] = source_kind
                st.session_state["native_resume_v31_source_label"] = uploaded.name[:240]
                st.session_state["native_resume_v31_last_upload_digest"] = digest
                st.session_state["native_resume_v31_truth_confirm"] = False
                v3._toast(
                    "success",
                    "Master Resume parsed and reflowed. Review the reconstructed evidence before saving it.",
                )

    if "native_resume_v31_source_draft" not in st.session_state:
        st.session_state["native_resume_v31_source_draft"] = str(source.get("content_text") or "")
    if "native_resume_v31_source_label" not in st.session_state:
        st.session_state["native_resume_v31_source_label"] = str(source.get("label") or "Master Resume")
    st.session_state.setdefault(
        "native_resume_v31_source_kind",
        str(source.get("source_kind") or "pasted_text"),
    )

    label = st.text_input("Source label", key="native_resume_v31_source_label", max_chars=240)
    source_text = st.text_area(
        "Extracted master-resume evidence",
        key="native_resume_v31_source_draft",
        height=360,
        placeholder="Upload a PDF/DOCX or paste the complete truthful resume/career-history source here...",
        help=(
            "This text is the evidence boundary for profile extraction and JD writing. It should read as coherent sections and paragraphs, "
            "not one token per line. Review it before confirming."
        ),
    )
    confirmed = st.checkbox(
        "I confirm this source is truthful and MUNSHI may use it for profile extraction and JD-specific resume writing.",
        key="native_resume_v31_truth_confirm",
    )
    save_columns = st.columns((1.2, 2.8), gap="small")
    with save_columns[0]:
        if st.button(
            "Save master source",
            type="primary",
            use_container_width=True,
            key="native_resume_v31_save_source",
            disabled=not (confirmed and source_text.strip()),
        ):
            try:
                v3.save_confirmed_source(
                    content_text=source_text,
                    label=label or "Master Resume",
                    source_kind=str(
                        st.session_state.get("native_resume_v31_source_kind") or "pasted_text"
                    ),
                )
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                # Do not touch source_draft/source_label/truth_confirm here:
                # their widgets already exist in this render pass.  Mark a
                # non-widget flag and synchronize those keys before widgets are
                # created on the rerun instead.
                mark_source_widget_refresh_pending(st.session_state)
                v3._toast("success", "Confirmed Master Resume source saved.")
                st.rerun()
    with save_columns[1]:
        if source:
            st.caption(
                f"Active source: {source.get('label') or 'Master Resume'} · "
                f"SHA-256 {str(source.get('content_sha256') or '')[:12]}… · "
                f"updated {source.get('updated_at') or 'date not recorded'}"
            )
        else:
            st.caption("No confirmed Master Resume source is saved yet.")

    current = v3.active_source()
    if current:
        try:
            bundle = v3.build_evidence_bundle(source_id=str(current["source_id"]))
        except (LookupError, ValueError, RuntimeError) as error:
            st.warning(str(error))
        else:
            facts = st.columns(3, gap="small")
            facts[0].metric("Evidence records", len(bundle["items"]))
            facts[1].metric("Sensitive self-ID", "Excluded from AI")
            facts[2].metric("Evidence digest", str(bundle["evidence_digest"])[:10] + "…")
    return current
