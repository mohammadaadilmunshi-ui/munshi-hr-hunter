"""Secure integration setup and readiness UI.

Inputs are write-only and stored through the AES-GCM vault. The page never reads
stored plaintext back into Streamlit. External Cloudflare mail-secret sync is
reported separately because it requires Cloudflare authorization outside Hunter.
"""
from __future__ import annotations

import re
from typing import Any

import streamlit as st

from app import application_notifications_runtime_v3 as notifications
from app.integration_credentials_v1 import (
    SMTP2GO_API_KEY_TYPE,
    SMTP2GO_SENDER_TYPE,
    TELEGRAM_BOT_TOKEN_TYPE,
    TELEGRAM_CHAT_ID_TYPE,
    integration_readiness,
)
from app.secure_vault import VaultError, store_secret, vault_readiness

_ACTOR = "streamlit:integration-credentials"
_EMAIL = re.compile(r"^[^\s@\\]+@[^\s@\\]+\.[^\s@\\]+$")


def _status_card(title: str, ready: bool, detail: str) -> None:
    label = "Ready" if ready else "Setup needed"
    icon = "✓" if ready else "!"
    st.markdown(
        f"""<div class="settings-v22-card">
        <b>{icon} {title}</b>
        <span><strong>{label}</strong></span>
        <span>{detail}</span>
        </div>""",
        unsafe_allow_html=True,
    )


def _validate_token(value: str) -> str:
    value = str(value or "").strip()
    if len(value) < 20 or ":" not in value or any(ch.isspace() for ch in value):
        raise ValueError("Telegram bot token does not look valid.")
    return value


def _validate_chat_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or not value.lstrip("-").isdigit():
        raise ValueError("Telegram chat ID must be numeric.")
    return value


def _validate_api_key(value: str) -> str:
    value = str(value or "").strip()
    if len(value) < 16 or any(ch.isspace() for ch in value):
        raise ValueError("SMTP2GO API key does not look valid.")
    return value


def _validate_sender(value: str) -> str:
    value = str(value or "").strip().lower()
    if not _EMAIL.fullmatch(value):
        raise ValueError("SMTP sender must be a valid email address.")
    return value


def _validated_smtp_fields(
    api_key: str, sender: str, *, existing_api_key: bool = False
) -> tuple[str | None, str]:
    """Validate both fields before writing either vault secret.

    An existing key is write-only: a blank input means keep it unchanged.
    """
    normalized_sender = _validate_sender(sender)
    normalized_key = _validate_api_key(api_key) if str(api_key or "").strip() else None
    if normalized_key is None and not existing_api_key:
        raise ValueError("Enter the SMTP2GO API key before saving the sender.")
    return normalized_key, normalized_sender


def _notification_preferences() -> None:
    preference = notifications.get_preference()
    st.markdown("### What should MUNSHI notify you about?")
    st.caption(
        "Notifications never contain ATS passwords, OTPs, magic links, "
        "verification codes, credential references, or submit authority."
    )
    with st.form("integration_notification_preferences"):
        destination = st.text_input(
            "Notification email",
            value=str(preference.get("destination_email") or ""),
            placeholder="you@example.com",
        )
        submitted = st.checkbox(
            "Verified application submission",
            value=bool(preference.get("submission_email_enabled")),
        )
        confirmed = st.checkbox(
            "Verified employer application-confirmation email",
            value=bool(preference.get("employer_confirmation_email_enabled")),
        )
        communications = st.checkbox(
            "Recruiter, interview, assessment, rejection, and offer updates",
            value=bool(preference.get("communication_email_enabled")),
        )
        if st.form_submit_button(
            "Save notification preferences",
            type="primary",
            use_container_width=True,
        ):
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
                st.rerun()


def render_integration_credentials() -> None:
    st.divider()
    st.markdown("## Automation integrations")
    st.caption(
        "Configure optional delivery channels without putting secrets in Git, "
        "Compose files, or dashboard settings."
    )
    vault = vault_readiness()
    readiness = integration_readiness()
    columns = st.columns(3, gap="small")
    with columns[0]:
        telegram = readiness["telegram"]
        _status_card(
            "Telegram",
            bool(telegram["configured"]),
            "Bot token + authorized chat are available."
            if telegram["configured"]
            else "Store a bot token and chat ID below.",
        )
    with columns[1]:
        smtp = readiness["smtp2go"]
        _status_card(
            "SMTP notifications",
            bool(smtp["configured"]),
            "SMTP2GO delivery credentials are available."
            if smtp["configured"]
            else "Store an SMTP2GO API key and verified sender below.",
        )
    with columns[2]:
        mail = readiness["mail_router"]
        mail_ready = bool(
            mail["hunter_signing_secret_configured"]
            and mail["external_sync_confirmed"]
        )
        _status_card(
            "Inbound mail signing",
            mail_ready,
            "Hunter secret and external Worker sync are confirmed."
            if mail_ready
            else (
                "Hunter signing secret is ready; Cloudflare Worker sync still "
                "needs external proof."
                if mail["hunter_signing_secret_configured"]
                else "Hunter mail signing secret is not configured."
            ),
        )

    if not vault.get("available"):
        st.error(
            "Encrypted vault is unavailable. Integration credential entry is "
            "disabled until the server vault is healthy."
        )
        return

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("### Telegram")
        st.caption(
            "Write-only setup. Stored secrets are never displayed back in the browser."
        )
        with st.form("integration_telegram_credentials", clear_on_submit=True):
            bot_token = st.text_input(
                "Bot token",
                type="password",
                autocomplete="off",
                placeholder="123456789:AA…",
            )
            chat_id = st.text_input(
                "Authorized chat ID",
                type="password",
                autocomplete="off",
                placeholder="-100… or numeric user/chat ID",
            )
            if st.form_submit_button(
                "Store Telegram credentials",
                type="primary",
                use_container_width=True,
            ):
                try:
                    store_secret(
                        TELEGRAM_BOT_TOKEN_TYPE,
                        _validate_token(bot_token),
                        actor=_ACTOR,
                    )
                    store_secret(
                        TELEGRAM_CHAT_ID_TYPE,
                        _validate_chat_id(chat_id),
                        actor=_ACTOR,
                    )
                except (ValueError, VaultError) as error:
                    st.error(str(error))
                else:
                    st.success("Telegram credentials encrypted and stored.")
                    st.rerun()

    with right:
        st.markdown("### SMTP2GO")
        st.caption("Used only for notification preferences you explicitly enable.")
        existing_api_key = smtp["key_source"] != "missing"
        if existing_api_key:
            st.caption("An API key is already stored securely. Leave the key field blank to keep it.")
        with st.form("integration_smtp_credentials", clear_on_submit=True):
            api_key = st.text_input(
                "SMTP2GO API key",
                type="password",
                autocomplete="off",
                placeholder="Leave blank to keep saved key" if existing_api_key else "Paste API key",
            )
            sender = st.text_input(
                "Verified sender email",
                placeholder="notifications@example.com",
            )
            if st.form_submit_button(
                "Store SMTP credentials",
                type="primary",
                use_container_width=True,
            ):
                try:
                    validated_key, validated_sender = _validated_smtp_fields(
                        api_key, sender, existing_api_key=existing_api_key
                    )
                    if validated_key is not None:
                        store_secret(
                            SMTP2GO_API_KEY_TYPE, validated_key, actor=_ACTOR
                        )
                    store_secret(
                        SMTP2GO_SENDER_TYPE, validated_sender, actor=_ACTOR
                    )
                except (ValueError, VaultError) as error:
                    st.error(str(error))
                else:
                    st.success("SMTP2GO credentials encrypted and stored.")
                    st.rerun()

    _notification_preferences()

    mail = readiness["mail_router"]
    st.markdown("### Inbound application mail")
    if mail["hunter_signing_secret_configured"]:
        st.success("Hunter inbound-mail signing secret is configured.")
    else:
        st.error("Hunter inbound-mail signing secret is missing.")
    if mail["external_sync_confirmed"]:
        st.success("Cloudflare Worker signing-secret sync is marked confirmed.")
    else:
        st.warning(
            "Cloudflare Worker signing-secret sync is not yet proven. "
            "Do not treat inbound mail as end-to-end ready until the same secret "
            "is installed on munshi-mail-router and a signed test is accepted."
        )


def install_integration_credentials_ui(pages_module: Any) -> None:
    if getattr(pages_module, "_integration_credentials_ui_v1_installed", False):
        return
    original_settings = pages_module.settings

    def settings_with_integrations() -> None:
        original_settings()
        if st.session_state.get("product_settings_section") == "Integrations":
            render_integration_credentials()

    pages_module.settings = settings_with_integrations
    pages_module._integration_credentials_ui_v1_installed = True
