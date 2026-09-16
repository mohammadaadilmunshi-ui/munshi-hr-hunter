"""Additive Streamlit projection for application evidence and email preferences."""
from __future__ import annotations

from typing import Any, Callable

import streamlit as st

from app import application_notifications_runtime_v2 as notifications


def _render_evidence() -> None:
    rows = notifications.application_evidence_rows(limit=100)
    st.markdown("### Verified application evidence")
    if not rows:
        st.caption(
            "Verified submissions will appear here after a production receipt is ingested. "
            "Employer email confirmation is shown only after independently correlated mail evidence."
        )
        return
    st.dataframe(
        [
            {
                "Company": row["company"],
                "Role": row["title"],
                "Submission": row["submitted_label"],
                "Employer email": row["employer_email_label"],
                "Provider": row["provider"],
                "Submitted at": row["submitted_at"],
                "Email confirmed at": row["employer_email_confirmed_at"],
            }
            for row in rows
        ],
        hide_index=True,
        use_container_width=True,
    )


def _render_settings() -> None:
    preference = notifications.get_preference()
    st.divider()
    st.markdown("### Application email notifications")
    st.caption(
        "This is your user-facing notification address. MUNSHI sends verified submission "
        "and employer-confirmation updates here. ATS verification links, emailed codes, "
        "password-reset links, magic-login links, passwords, credential references, and "
        "submit authority stay inside MUNSHI and are never forwarded."
    )
    with st.form("application_notification_preferences_v1"):
        destination = st.text_input(
            "Notification email",
            value=str(preference.get("destination_email") or ""),
            placeholder="you@example.com",
        )
        submitted = st.checkbox(
            "Email me when MUNSHI has a verified submission receipt",
            value=bool(preference.get("submission_email_enabled")),
        )
        confirmed = st.checkbox(
            "Email me when an employer application-confirmation email is verified",
            value=bool(preference.get("employer_confirmation_email_enabled")),
        )
        if st.form_submit_button("Save notification preferences", type="primary"):
            try:
                notifications.save_preference(
                    destination_email=destination,
                    submission_email_enabled=submitted,
                    employer_confirmation_email_enabled=confirmed,
                )
            except (ValueError, notifications.NotificationError) as error:
                st.error(str(error))
            else:
                st.success("Notification preferences saved.")


def _wrap(base: Callable[[], None], suffix: Callable[[], None]) -> Callable[[], None]:
    def rendered() -> None:
        base()
        suffix()

    return rendered


def install_application_notification_ui(product_pages: Any) -> None:
    """Install after the final workspace wrapper so reruns keep the projection."""
    if bool(getattr(product_pages, "_application_notification_ui_v1_installed", False)):
        return
    product_pages.dashboard = _wrap(product_pages.dashboard, _render_evidence)
    product_pages.tracker = _wrap(product_pages.tracker, _render_evidence)
    product_pages.settings = _wrap(product_pages.settings, _render_settings)
    product_pages._application_notification_ui_v1_installed = True
