"""AutoApply product settings layered over the established V2.2 dashboard.

This module is intentionally additive. It exposes user-visible preferences and
credential readiness without granting submission authority during page render.
Secrets are accepted only when the existing encrypted server vault is available,
and are never read back into Streamlit.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from typing import Any

import streamlit as st

from app.database import get_connection, get_setting, save_setting
from app.product_v22 import _extension_profile_record, advanced_v22
from app.secure_vault import VaultError, delete_secret, store_secret, vault_available


_PAGES: Any = None
_SETTINGS_KEY = "product_autoapply_preferences_v1"
_ANTHROPIC_CREDENTIAL_TYPE = "autoapply_anthropic_api_key"
_AUTH_MODES = {"subscription", "api"}
_MODELS = ("sonnet", "opus", "haiku")
_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "auth_mode": "subscription",
    "model": "sonnet",
    "headless": False,
    "max_turns": 40,
    "max_cost_per_application_usd": 1.0,
    "allow_final_submit": False,
}


def _normalized_preferences(value: Any = None) -> dict[str, Any]:
    raw = dict(value if isinstance(value, dict) else get_setting(_SETTINGS_KEY, {}) or {})
    auth_mode = str(raw.get("auth_mode") or _DEFAULTS["auth_mode"]).strip().casefold()
    if auth_mode not in _AUTH_MODES:
        auth_mode = "subscription"
    model = str(raw.get("model") or _DEFAULTS["model"]).strip().casefold()
    if model not in _MODELS:
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
        "allow_final_submit": bool(raw.get("allow_final_submit", False)),
    }


def _save_preferences(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_preferences(value)
    save_setting(
        _SETTINGS_KEY,
        normalized,
        changed_by="streamlit:autoapply-settings",
    )
    return normalized


def _vault_secret_exists(credential_type: str) -> bool:
    """Return metadata-only readiness; never decrypt or return a credential."""
    connection = get_connection()
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='credential_secret'"
        ).fetchone()
        if not table:
            return False
        row = connection.execute(
            "SELECT 1 FROM credential_secret WHERE credential_type=? AND account_label='default' LIMIT 1",
            (credential_type,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _runtime_health() -> dict[str, Any]:
    """Read only the non-secret Apply health endpoint with a short timeout."""
    explicit = str(os.getenv("MUNSHI_APPLY_RUNTIME_HEALTH_URL") or "").strip()
    staging = str(os.getenv("CLOUD_SHADOW_MODE") or "").strip().casefold() == "true"
    url = explicit or ("http://apply:8000/health" if staging else "")
    if not url:
        return {"configured": False, "reachable": False, "url": ""}
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=1.25) as response:  # noqa: S310
            if int(response.status) != 200:
                return {"configured": True, "reachable": False, "url": url}
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError):
        return {"configured": True, "reachable": False, "url": url}
    return {
        "configured": True,
        "reachable": payload.get("status") == "healthy" and payload.get("database") == "healthy",
        "url": url,
        "schema_version": payload.get("schema_version"),
        "migration_count": payload.get("migration_count"),
        "version": payload.get("version"),
    }


def _status_card(title: str, value: str, detail: str) -> None:
    st.markdown(
        f"""<div class="settings-v22-card">
            <b>{_PAGES.esc(title)}</b>
            <span><strong>{_PAGES.esc(value)}</strong></span>
            <span>{_PAGES.esc(detail)}</span>
        </div>""",
        unsafe_allow_html=True,
    )


def _render_autoapply_summary(preferences: dict[str, Any]) -> None:
    runtime = _runtime_health()
    runtime_value = "Healthy" if runtime.get("reachable") else "Not connected"
    runtime_detail = (
        f"Apply service · {runtime.get('schema_version') or 'schema not reported'}"
        if runtime.get("reachable")
        else "The dashboard cannot currently confirm the Apply runtime health endpoint."
    )
    credential_ready = (
        preferences["auth_mode"] == "subscription"
        or _vault_secret_exists(_ANTHROPIC_CREDENTIAL_TYPE)
    )
    columns = st.columns(4, gap="small")
    with columns[0]:
        _status_card(
            "AutoApply",
            "Enabled" if preferences["enabled"] else "Disabled",
            "Website preference only; runtime safety gates remain authoritative.",
        )
    with columns[1]:
        _status_card("Apply runtime", runtime_value, runtime_detail)
    with columns[2]:
        _status_card(
            "Claude authentication",
            "Ready" if credential_ready else "Needs setup",
            "Subscription login" if preferences["auth_mode"] == "subscription" else "Encrypted API credential",
        )
    with columns[3]:
        _status_card(
            "Final submit",
            "Requested" if preferences["allow_final_submit"] else "Off",
            "Still requires Application Plan authority and the Apply runtime submit gate.",
        )


def _render_autoapply_preferences() -> None:
    preferences = _normalized_preferences()
    st.markdown("### Autonomous Apply")
    st.markdown(
        '<div class="settings-v22-copy">Configure the browser application executor here. '
        'These preferences do not bypass Candidate Truth, Application Plan, security '
        'checkpoints, review digests, provider policy, or independent submission verification.'</n        '</div>',
        unsafe_allow_html=True,
    )
    _render_autoapply_summary(preferences)

    with st.form("product_v23_autoapply_preferences"):
        enabled = st.toggle(
            "Enable autonomous browser executor",
            value=preferences["enabled"],
            help="Allows eligible Application Plans to use the autonomous browser executor after all upstream gates pass.",
        )
        left, right = st.columns(2, gap="medium")
        with left:
            auth_mode = st.selectbox(
                "Claude authentication",
                ["subscription", "api"],
                index=0 if preferences["auth_mode"] == "subscription" else 1,
                format_func=lambda value: (
                    "Claude subscription login · no server API key required"
                    if value == "subscription"
                    else "Anthropic API key · encrypted server vault"
                ),
            )
            model = st.selectbox(
                "Browser-agent model",
                list(_MODELS),
                index=list(_MODELS).index(preferences["model"]),
                format_func=lambda value: value.title(),
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
            allow_final_submit = st.toggle(
                "Permit final submit when the Application Plan also authorizes it",
                value=preferences["allow_final_submit"],
                help="This is not sufficient by itself. The Apply runtime must separately enable final submission and independently verify the reviewed target.",
            )
        submitted = st.form_submit_button(
            "Save AutoApply settings",
            type="primary",
            use_container_width=True,
        )
        if submitted:
            saved = _save_preferences(
                {
                    "enabled": enabled,
                    "auth_mode": auth_mode,
                    "model": model,
                    "headless": headless,
                    "max_turns": max_turns,
                    "max_cost_per_application_usd": max_cost,
                    "allow_final_submit": allow_final_submit,
                }
            )
            st.success(
                "AutoApply preferences saved. "
                + (
                    "Final-submit preference is recorded but does not override the runtime submit gate."
                    if saved["allow_final_submit"]
                    else "Final submit remains off in this preference."
                )
            )


def _render_anthropic_credential() -> None:
    preferences = _normalized_preferences()
    available = vault_available()
    configured = _vault_secret_exists(_ANTHROPIC_CREDENTIAL_TYPE)
    st.markdown("### Claude credential")

    if preferences["auth_mode"] == "subscription":
        st.info(
            "Subscription mode does not require an Anthropic API key in this website. "
            "The paired MUNSHI Apply executor must have an authenticated Claude Code session."
        )
    elif not available:
        st.warning(
            "API-key mode is selected, but encrypted credential storage is not configured on this server. "
            "The key field stays disabled until the vault is available."
        )
    else:
        st.success(
            "Anthropic API credential is encrypted in the server vault."
            if configured
            else "Encrypted server vault is ready for an Anthropic API credential."
        )

    with st.form("product_v23_anthropic_credential"):
        secret = st.text_input(
            "Anthropic API key",
            value="",
            type="password",
            placeholder="Stored value is never displayed",
            disabled=(not available or preferences["auth_mode"] != "api"),
            help="Saving replaces the encrypted credential. MUNSHI never displays the stored plaintext value.",
        )
        store = st.form_submit_button(
            "Store encrypted API key" if not configured else "Replace encrypted API key",
            type="primary",
            disabled=(not available or preferences["auth_mode"] != "api" or not secret.strip()),
            use_container_width=True,
        )
        if store:
            try:
                store_secret(_ANTHROPIC_CREDENTIAL_TYPE, secret.strip())
            except VaultError as error:
                st.error(str(error))
            else:
                st.success("Anthropic API credential stored. The plaintext value is not retained in page state.")
                st.rerun()

    if configured and available:
        if st.button(
            "Delete stored Anthropic API credential",
            key="product_v23_delete_anthropic",
            use_container_width=True,
        ):
            delete_secret(_ANTHROPIC_CREDENTIAL_TYPE)
            st.success("Stored Anthropic API credential deleted.")
            st.rerun()


def _render_security_challenge_policy() -> None:
    st.markdown("### Security checkpoint continuation")
    st.markdown(
        '<div class="settings-v22-card"><b>User-assisted verification · automatic continuation</b>'
        '<span>If an employer site presents CAPTCHA, MFA, OTP, identity, or another protected '
        'verification step, MUNSHI pauses and checkpoints the exact Chromium session. The website '
        'shows an action-required popup, focuses that exact tab, and resumes only after the extension '
        're-scans the page and confirms the checkpoint is gone.</span></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Challenge answers, passwords, OTP values, cookies, and browser session secrets are not copied into this Settings page."
    )


def _render_credentials_section() -> None:
    preferences = _normalized_preferences()
    st.markdown("### Credentials & AutoApply")
    available = vault_available()
    if available:
        st.success("Encrypted credential storage is available on this server.")
    else:
        st.warning(
            "Encrypted credential storage is not configured on this server. "
            "Subscription mode can still be selected, but API-key entry remains disabled."
        )

    st.markdown(
        '<div class="settings-v22-card"><b>Credential policy</b>'
        '<span>Passwords and API keys are never displayed in plaintext. Secret entry is enabled only '
        'through the encrypted vault and explicit user actions. Runtime readiness and saved preference '
        'are shown separately so the UI never implies that a credential alone authorizes an application.</span></div>',
        unsafe_allow_html=True,
    )
    _render_autoapply_summary(preferences)
    _render_anthropic_credential()
    _render_security_challenge_policy()


def settings_v23() -> None:
    _PAGES.page_intro(
        "PREFERENCES",
        "Settings",
        "Readable product controls first. AutoApply configuration and credential readiness are visible here; engineering diagnostics remain under Advanced / System.",
    )
    options = [
        "Apply",
        "Automation",
        "Integrations",
        "Profile & defaults",
        "Credentials",
        "Advanced / System",
    ]
    left, content = st.columns((1.05, 3.95), gap="large")
    with left:
        section = st.radio(
            "Settings section",
            options,
            label_visibility="collapsed",
            key="product_settings_section",
            on_change=_PAGES._sync_subroute,
            args=(
                "section",
                "product_settings_section",
                {
                    "Apply": "apply",
                    "Automation": "automation",
                    "Integrations": "integrations",
                    "Profile & defaults": "profile",
                    "Credentials": "credentials",
                    "Advanced / System": "advanced",
                },
            ),
        )

    with content:
        if section == "Apply":
            policy = _PAGES.volume_policy()
            preferences = _normalized_preferences()
            st.markdown("### Review and submission authority")
            st.markdown(
                '<div class="settings-v22-card"><b>What this controls</b>'
                '<span>The review preference changes whether you want a review step before action. '
                'Targeting, work authorization, dedupe, ATS, Application Plan, provider safety, and '
                'independent verification remain authoritative.</span></div>',
                unsafe_allow_html=True,
            )
            with st.form("product_v23_review_form"):
                review = st.checkbox(
                    "Require review before action",
                    value=policy["review_first"],
                )
                if st.form_submit_button("Save review preference", type="primary"):
                    _PAGES.save_review_preference(review)
                    st.success("Review preference saved.")
            _status_card(
                "Final submit preference",
                "Requested" if preferences["allow_final_submit"] else "Off",
                "Change this under Credentials & AutoApply. Runtime submit authority remains a separate gate.",
            )

        elif section == "Automation":
            policy = _PAGES.volume_policy()
            current_lanes = _PAGES.lanes()
            preferences = _normalized_preferences()
            st.markdown("### Automation")
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>Current mode: {_PAGES.esc(policy["mode"].replace("_", " ").title())}</b>
                    <span>{sum(bool(lane["enabled"]) for lane in current_lanes)} of {len(current_lanes)} target lane(s) enabled. Unlimited means no MUNSHI business quota; provider and safety controls still apply.</span>
                </div>""",
                unsafe_allow_html=True,
            )
            _status_card(
                "Autonomous browser executor",
                "Enabled" if preferences["enabled"] else "Disabled",
                f"{preferences['model'].title()} · max {preferences['max_turns']} turns · ${preferences['max_cost_per_application_usd']:.2f} ceiling per application",
            )
            st.markdown(
                '<a class="product-nav-link" href="?view=settings&amp;section=credentials" '
                'target="_self">Configure AutoApply →</a>&nbsp;&nbsp;'
                '<a class="product-nav-link" href="?view=auto-prepare" '
                'target="_self">Open Auto Prepare →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Integrations":
            st.markdown("### Integrations")
            extension = _extension_profile_record()
            runtime = _runtime_health()
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>MUNSHI Apply runtime</b>
                    <span>{"Healthy staging/runtime service detected." if runtime.get("reachable") else "Runtime health is not currently confirmed from this dashboard."}</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>MUNSHI Apply profile bridge</b>
                    <span>{"Server-side MasterProfile data is available." if extension else "No server-side MasterProfile sync record is available. The browser extension keeps its MasterProfile locally until a secure sync bridge is enabled."}</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<div class="settings-v22-card"><b>Gmail read-only</b>'
                '<span>Connect Gmail to synchronize application confirmations, assessments, interviews, offers, rejections, and reminders. This integration does not send email.</span></div>',
                unsafe_allow_html=True,
            )
            _PAGES._inbox()

        elif section == "Profile & defaults":
            facts = _PAGES.candidate_facts()
            designated = _PAGES.master_resume()
            st.markdown("### Profile & defaults")
            st.markdown(
                f"""<div class="settings-v22-card">
                    <b>{len(facts)} structured profile fact(s)</b>
                    <span>Master resume: {"configured" if designated else "not configured"}. Open Profile to review source-backed facts and prepared artifacts.</span>
                </div>""",
                unsafe_allow_html=True,
            )
            st.markdown(
                '<a class="product-nav-link" href="?view=profile&amp;tab=details" target="_self">Open Profile details →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Credentials":
            _render_credentials_section()

        elif section == "Advanced / System":
            advanced_v22()


def install_autoapply_settings_ui(product_pages: Any) -> None:
    """Install only the user-facing Settings replacement after V2.2 is active."""
    global _PAGES
    _PAGES = product_pages
    product_pages.settings = settings_v23
