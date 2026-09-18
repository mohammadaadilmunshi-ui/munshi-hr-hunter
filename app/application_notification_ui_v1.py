"""Additive Streamlit projection for application evidence and email preferences."""
from __future__ import annotations

import os
from typing import Any, Callable

import streamlit as st

from app import application_notifications_runtime_v3 as notifications
from app.integration_credentials_v1 import smtp2go_configured


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


def _render_communications() -> None:
    rows = notifications.application_communication_rows(limit=100)
    st.markdown("### Recruiting communications")
    st.caption(
        "Read-only communication status from the MUNSHI application inbox. "
        "MUNSHI does not auto-reply to recruiters or employers."
    )
    if not rows:
        st.caption("Recruiter, interview, assessment, rejection, and offer updates will appear here.")
        return
    st.dataframe(
        [
            {
                "Type": row["category"].replace("_", " ").title(),
                "Company": row["company"] or "Unmatched",
                "Role": row["title"] or "Unmatched",
                "Sender domain": row["sender_domain"] or "Unknown",
                "Received at": row["received_at"],
                "Application": row["correlation"],
                "Auto reply": "Disabled",
            }
            for row in rows
        ],
        hide_index=True,
        use_container_width=True,
    )


def _render_application_activity() -> None:
    _render_evidence()
    st.divider()
    _render_communications()


def _delivery_ready() -> bool:
    enabled = str(
        os.getenv("MUNSHI_APPLICATION_EMAIL_NOTIFICATIONS_ENABLED") or ""
    ).strip().casefold() in {"1", "true", "yes", "on"}
    return enabled or smtp2go_configured()


def render_application_notification_settings() -> None:
    preference = notifications.get_preference()
    st.divider()
    st.markdown(
        '<div id="application-email-notifications"></div>',
        unsafe_allow_html=True,
    )
    st.markdown("### Application email notifications")
    st.caption(
        "This is your user-facing notification address. MUNSHI sends verified submission, "
        "employer-confirmation, and optional recruiting-status updates here. ATS verification "
        "links, emailed codes, password-reset links, magic-login links, passwords, credential "
        "references, raw inbound mail, and submit authority stay inside MUNSHI and are never "
        "forwarded."
    )
    if _delivery_ready():
        st.success("Outbound application-email notifications are ready.")
    else:
        st.warning(
            "Notification preferences can be saved, but outbound delivery is not "
            "configured yet. Configure SMTP2GO under Integrations before relying "
            "on email delivery."
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
        communications = st.checkbox(
            "Email me about recruiter, interview, assessment, rejection, and offer updates",
            value=bool(preference.get("communication_email_enabled")),
        )
        if st.form_submit_button("Save notification preferences", type="primary"):
            try:
                notifications.save_preference(
                    destination_email=destination,
                    submission_email_enabled=submitted,
                    employer_confirmation_email_enabled=confirmed,
                    communication_email_enabled=communications,
                )
            except (ValueError, notifications.NotificationError) as error:
                st.error(str(error))
            else:
                st.success("Notification preferences saved.")


def _wrap_once(
    base: Callable[[], None],
    suffix: Callable[[], None],
    marker: str,
) -> Callable[[], None]:
    if bool(getattr(base, marker, False)):
        return base

    def rendered() -> None:
        base()
        suffix()

    setattr(rendered, marker, True)
    return rendered


def install_application_notification_ui(product_pages: Any) -> None:
    """Rerun-safe Dashboard/Tracker projection.

    Settings is rendered directly by autoapply_settings_ui.settings_v23 so the
    canonical Settings function cannot accumulate persistent wrapper layers.
    """
    product_pages.dashboard = _wrap_once(
        product_pages.dashboard,
        _render_application_activity,
        "_application_notification_activity_v1",
    )
    product_pages.tracker = _wrap_once(
        product_pages.tracker,
        _render_application_activity,
        "_application_notification_activity_v1",
    )
    product_pages._application_notification_ui_v1_installed = True
