"""Secure API-key vault and AutoApply readiness controls for the initial dashboard.

This layer is deliberately additive. It appends to the established V2.2
Credentials page, stores only encrypted secrets through ``app.secure_vault``,
and never reads a saved secret back into Streamlit. Rendering this page cannot
start an application or grant final-submit authority.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import streamlit as st

from app.database import get_setting, save_setting
from app.secure_vault import (
    VaultError,
    delete_secret,
    secret_metadata,
    store_secret,
    vault_readiness,
)

_PAGES: Any = None
_SETTINGS_KEY = "product_autoapply_preferences_v1"
_ACTOR = "streamlit:secure-api-vault"


@dataclass(frozen=True)
class CredentialSpec:
    slug: str
    title: str
    credential_type: str
    purpose: str
    placeholder: str


_CREDENTIALS = (
    CredentialSpec(
        slug="anthropic",
        title="Anthropic / Claude",
        credential_type="autoapply_anthropic_api_key",
        purpose="Autonomous browser executor when API authentication is selected.",
        placeholder="Paste Anthropic API key",
    ),
    CredentialSpec(
        slug="openai",
        title="OpenAI",
        credential_type="openai_api_key",
        purpose="Reserved for MUNSHI server-side AI features that explicitly select OpenAI.",
        placeholder="Paste OpenAI API key",
    ),
    CredentialSpec(
        slug="gemini",
        title="Google Gemini",
        credential_type="gemini_api_key",
        purpose="Reserved for MUNSHI server-side AI features that explicitly select Gemini.",
        placeholder="Paste Gemini API key",
    ),
)


def _bool_env(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _preferences(value: Any = None) -> dict[str, Any]:
    raw = dict(value if isinstance(value, dict) else get_setting(_SETTINGS_KEY, {}) or {})
    auth_mode = str(raw.get("auth_mode") or "subscription").strip().casefold()
    if auth_mode not in {"subscription", "api"}:
        auth_mode = "subscription"
    model = str(raw.get("model") or "sonnet").strip().casefold()
    if model not in {"sonnet", "opus", "haiku"}:
        model = "sonnet"
    try:
        max_turns = max(1, min(int(raw.get("max_turns", 40)), 200))
    except (TypeError, ValueError):
        max_turns = 40
    try:
        max_cost = max(0.0, min(float(raw.get("max_cost_per_application_usd", 1.0)), 100.0))
    except (TypeError, ValueError):
        max_cost = 1.0
    return {
        "enabled": bool(raw.get("enabled", False)),
        "auth_mode": auth_mode,
        "model": model,
        "headless": bool(raw.get("headless", False)),
        "max_turns": max_turns,
        "max_cost_per_application_usd": round(max_cost, 2),
        # This dashboard tranche intentionally never grants final-submit authority.
        "allow_final_submit": False,
    }


def _configured(credential_type: str) -> bool:
    try:
        return secret_metadata(credential_type) is not None
    except (VaultError, Exception):
        return False


def _validate_api_key(value: str) -> str:
    value = str(value or "").strip()
    if len(value) < 16:
        raise ValueError("API key looks too short. Check the value and try again.")
    if len(value) > 16384:
        raise ValueError("API key is too large.")
    if any(character.isspace() for character in value):
        raise ValueError("API keys cannot contain whitespace.")
    if any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ValueError("API key contains unsupported characters.")
    return value


def _auth_ready(preferences: dict[str, Any]) -> bool:
    if preferences["auth_mode"] == "subscription":
        # Website configuration is valid; the Apply executor independently proves
        # the actual Claude Code session before a browser run.
        return True
    return _configured("autoapply_anthropic_api_key")


def _card(title: str, value: str, detail: str) -> None:
    esc = getattr(_PAGES, "esc", lambda value: str(value))
    st.markdown(
        f"""<div class="settings-v22-card">
        <b>{esc(title)}</b>
        <span><strong>{esc(value)}</strong></span>
        <span>{esc(detail)}</span>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_readiness() -> None:
    readiness = vault_readiness()
    preferences = _preferences()
    acceptance_passed = _bool_env("MUNSHI_AUTOAPPLY_ACCEPTANCE_PASSED")
    auth_ready = _auth_ready(preferences)
    configuration_ready = bool(preferences["enabled"] and auth_ready)
    production_ready = bool(
        configuration_ready
        and readiness.get("production_ready")
        and acceptance_passed
    )

    st.markdown("### Security & release readiness")
    columns = st.columns(4, gap="small")
    with columns[0]:
        _card(
            "Encrypted vault",
            "Ready" if readiness.get("available") else "Unavailable",
            f"AES-256-GCM · key source: {readiness.get('key_source', 'none')}",
        )
    with columns[1]:
        _card(
            "Stored API keys",
            str(readiness.get("secret_count", 0)),
            "Metadata count only. Secret values are never returned to this page.",
        )
    with columns[2]:
        _card(
            "Test-run configuration",
            "Ready" if configuration_ready else "Needs setup",
            "Executor preference + authentication selection only; runtime proves itself separately.",
        )
    with columns[3]:
        _card(
            "Production release gate",
            "Ready" if production_ready else "Locked",
            (
                "Vault policy and acceptance gate passed."
                if production_ready
                else "Requires hardened vault + completed AutoApply acceptance campaign."
            ),
        )

    if readiness.get("available") and not readiness.get("production_ready"):
        st.warning(str(readiness.get("reason") or "Vault is not production-ready."))


def _render_autoapply_preferences() -> None:
    preferences = _preferences()
    st.markdown("### AutoApply test configuration")
    st.caption(
        "These are preparation/executor preferences. They do not grant final-submit "
        "authority and they do not bypass Application Plan, Candidate Truth, provider "
        "policy, security checkpoints, or independent verification."
    )

    with st.form("initial_dashboard_autoapply_preferences"):
        enabled = st.toggle(
            "Enable autonomous browser executor for test runs",
            value=preferences["enabled"],
        )
        left, right = st.columns(2, gap="medium")
        with left:
            auth_mode = st.selectbox(
                "Claude authentication",
                ["subscription", "api"],
                index=0 if preferences["auth_mode"] == "subscription" else 1,
                format_func=lambda value: (
                    "Claude subscription login · no hosted API key required"
                    if value == "subscription"
                    else "Anthropic API key · encrypted dashboard vault"
                ),
            )
            model = st.selectbox(
                "Browser-agent model",
                ["sonnet", "opus", "haiku"],
                index=["sonnet", "opus", "haiku"].index(preferences["model"]),
                format_func=str.title,
            )
            headless = st.toggle(
                "Run browser headless when supported",
                value=preferences["headless"],
            )
        with right:
            max_turns = st.number_input(
                "Maximum agent turns per application",
                min_value=1,
                max_value=200,
                value=int(preferences["max_turns"]),
                step=1,
            )
            max_cost = st.number_input(
                "Maximum AI cost per application (USD)",
                min_value=0.0,
                max_value=100.0,
                value=float(preferences["max_cost_per_application_usd"]),
                step=0.05,
                format="%.2f",
            )
            st.toggle(
                "Final submit",
                value=False,
                disabled=True,
                help="Locked until the production acceptance gate is explicitly passed.",
            )

        submitted = st.form_submit_button(
            "Save secure AutoApply settings",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            if auth_mode == "api" and not _configured("autoapply_anthropic_api_key"):
                st.error(
                    "Store an Anthropic API key in the encrypted vault before enabling API mode."
                )
            else:
                save_setting(
                    _SETTINGS_KEY,
                    {
                        "enabled": bool(enabled),
                        "auth_mode": auth_mode,
                        "model": model,
                        "headless": bool(headless),
                        "max_turns": int(max_turns),
                        "max_cost_per_application_usd": round(float(max_cost), 2),
                        "allow_final_submit": False,
                    },
                    changed_by=_ACTOR,
                )
                st.success("AutoApply test configuration saved. Final submit remains locked.")
                st.rerun()


def _render_credential(spec: CredentialSpec) -> None:
    readiness = vault_readiness()
    metadata = secret_metadata(spec.credential_type) if readiness.get("available") else None
    configured = metadata is not None

    with st.expander(
        f"{'✓' if configured else '○'} {spec.title}",
        expanded=spec.slug == "anthropic" and not configured,
    ):
        st.caption(spec.purpose)
        if configured:
            st.success(
                "Encrypted credential configured. Stored plaintext is intentionally not displayable."
            )
            st.caption(
                f"Updated: {metadata.get('updated_at') or 'unknown'} · "
                f"Algorithm: {metadata.get('algorithm_version') or 'unknown'}"
            )
        else:
            st.info("No credential stored.")

        if not readiness.get("available"):
            st.warning(
                "The vault master key is not configured. API-key entry remains disabled."
            )
            return

        with st.form(f"secure_api_vault_store_{spec.slug}", clear_on_submit=True):
            secret = st.text_input(
                "Replace API key" if configured else "API key",
                type="password",
                autocomplete="off",
                placeholder=spec.placeholder,
                help="Write-only field. MUNSHI never fills a stored secret back into the browser.",
                key=f"secure_api_vault_secret_{spec.slug}",
            )
            save = st.form_submit_button(
                "Replace encrypted key" if configured else "Store encrypted key",
                type="primary",
                use_container_width=True,
            )
            if save:
                try:
                    normalized = _validate_api_key(secret)
                    store_secret(
                        spec.credential_type,
                        normalized,
                        actor=_ACTOR,
                    )
                except (ValueError, VaultError) as error:
                    st.error(str(error))
                else:
                    st.success(
                        f"{spec.title} credential stored with AES-256-GCM encryption."
                    )
                    st.rerun()

        if configured:
            with st.form(f"secure_api_vault_delete_{spec.slug}"):
                confirm = st.checkbox(
                    f"Confirm removal of the stored {spec.title} credential"
                )
                remove = st.form_submit_button(
                    "Delete encrypted credential",
                    use_container_width=True,
                )
                if remove:
                    if not confirm:
                        st.error("Confirm removal before deleting the credential.")
                    else:
                        delete_secret(spec.credential_type, actor=_ACTOR)
                        st.success(f"{spec.title} credential removed.")
                        st.rerun()


def render_secure_api_vault() -> None:
    st.divider()
    st.markdown("## Secure API Key Vault")
    st.caption(
        "API keys are encrypted server-side before SQLite storage. Inputs are "
        "write-only; this page receives only non-secret status metadata."
    )
    _render_readiness()

    readiness = vault_readiness()
    if readiness.get("available"):
        st.success("Encrypted credential storage is operational.")
    else:
        st.error(
            "Encrypted credential storage is not configured. Configure the vault "
            "master key before storing API keys."
        )

    st.markdown("### API credentials")
    for spec in _CREDENTIALS:
        _render_credential(spec)

    _render_autoapply_preferences()

    st.markdown("### Security checkpoint policy")
    st.info(
        "CAPTCHA, MFA, OTP, identity verification, and other protected security "
        "checkpoints remain user-assisted. MUNSHI checkpoints the session and "
        "continues only after the protected step is cleared."
    )


def install_secure_api_vault_ui(pages_module: Any) -> None:
    """Append the secure vault only to Settings → Credentials."""
    global _PAGES
    _PAGES = pages_module
    if getattr(pages_module, "_secure_api_vault_ui_installed", False):
        return

    original_settings = pages_module.settings

    def settings_with_secure_vault() -> None:
        original_settings()
        if st.session_state.get("product_settings_section") == "Credentials":
            render_secure_api_vault()

    pages_module.settings = settings_with_secure_vault
    pages_module._secure_api_vault_ui_installed = True
