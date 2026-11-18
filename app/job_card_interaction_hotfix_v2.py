"""Interaction-only hardening for the Opportunity Details drawer.

This layer does not change job state, preparation authority, targeting, or submission.
It only keeps Streamlit's native dialog dismiss control visible after the V2.4 drawer
CSS moves the dialog to a fixed right-side/mobile-fullscreen surface.
"""
from __future__ import annotations

import streamlit as st

VERSION = "job-card-interaction-hotfix-v2"
MARKER = "AADIL_JOB_CARD_VISIBLE_CLOSE_V2"


def interaction_css() -> str:
    return """
<style>
/* Keep Streamlit's native dialog dismiss control inside the fixed job drawer. */
[data-testid="stDialog"]:has(.job-detail-drawer-marker) button[aria-label="Close"],
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [data-testid="stBaseButton-headerNoPadding"]{
    position:fixed!important;
    top:max(.8rem,env(safe-area-inset-top))!important;
    right:max(.8rem,env(safe-area-inset-right))!important;
    z-index:100000!important;
    display:flex!important;
    align-items:center!important;
    justify-content:center!important;
    width:42px!important;
    height:42px!important;
    min-width:42px!important;
    min-height:42px!important;
    margin:0!important;
    padding:0!important;
    opacity:1!important;
    visibility:visible!important;
    pointer-events:auto!important;
    color:#142129!important;
    background:rgba(255,255,255,.94)!important;
    border:1px solid rgba(206,215,208,.96)!important;
    border-radius:999px!important;
    box-shadow:0 8px 24px rgba(20,33,41,.14)!important;
    backdrop-filter:blur(12px)!important;
    -webkit-backdrop-filter:blur(12px)!important;
}
[data-testid="stDialog"]:has(.job-detail-drawer-marker) button[aria-label="Close"] svg,
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [data-testid="stBaseButton-headerNoPadding"] svg{
    width:21px!important;
    height:21px!important;
    color:#142129!important;
    fill:currentColor!important;
    opacity:1!important;
}
/* Reserve space so the first drawer heading never sits beneath the close target. */
[data-testid="stDialog"]:has(.job-detail-drawer-marker) [data-testid="stDialogContent"]{
    padding-top:3.6rem!important;
}
</style>
        """


def inject_job_card_interaction_css() -> None:
    st.markdown(interaction_css(), unsafe_allow_html=True)


def status() -> dict[str, object]:
    return {
        "success": True,
        "version": VERSION,
        "marker": MARKER,
        "native_dialog_close_visible": True,
        "submission_authority": False,
    }
