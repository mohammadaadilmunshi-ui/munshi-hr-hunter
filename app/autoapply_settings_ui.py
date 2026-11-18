"""AutoApply controls for the established MUNSHI product Settings surface.

Non-secret preferences live in Hunter's canonical settings store. API credentials
are accepted only through the existing encrypted vault and are never read back
into the browser. Rendering this page never grants submission authority or starts
an application.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any

import httpx
import streamlit as st

from app.database import get_connection, get_setting, save_setting
from app.product_v22 import _extension_profile_record, advanced_v22
from app.secure_vault import VaultError, delete_secret, store_secret, vault_available


_PAGES: Any = None
_SETTINGS_KEY = "product_autoapply_preferences_v1"
_ANTHROPIC_CREDENTIAL_TYPE = "autoapply_anthropic_api_key"
_AUTH_MODES = ("subscription", "api")
_MODELS = ("sonnet", "opus", "haiku")


def _preferences(value: Any = None) -> dict[str, Any]:
    raw = dict(value if isinstance(value, dict) else get_setting(_SETTINGS_KEY, {}) or {})
    auth_mode = str(raw.get("auth_mode") or "subscription").strip().casefold()
    if auth_mode not in _AUTH_MODES:
        auth_mode = "subscription"
    model = str(raw.get("model") or "sonnet").strip().casefold()
    if model not in _MODELS:
        model = "sonnet"
    try:
        max_turns = max(1, min(int(raw.get("max_turns", 40)), 200))
    except (TypeError, ValueError):
        max_turns = 40
    try:
        max_cost = max(
            0.0,
            min(float(raw.get("max_cost_per_application_usd", 1.0)), 100.0),
        )
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


def _save_preferences(value: dict[str, Any]) -> None:
    save_setting(
        _SETTINGS_KEY,
        _preferences(value),
        changed_by="streamlit:autoapply-settings",
    )


def _vault_secret_exists(credential_type: str) -> bool:
    """Check only encrypted-vault metadata; never decrypt a stored secret."""
    connection = get_connection()
    try:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='credential_secret'"
        ).fetchone()
        if not table:
            return False
        row = connection.execute(
            "SELECT 1 FROM credential_secret "
            "WHERE credential_type=? AND account_label='default' LIMIT 1",
            (credential_type,),
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False
    finally:
        connection.close()


def _runtime_health() -> dict[str, Any]:
    """Read only the non-secret Apply health endpoint with a bounded timeout."""
    explicit = str(os.getenv("MUNSHI_APPLY_RUNTIME_HEALTH_URL") or "").strip()
    isolated = str(os.getenv("CLOUD_SHADOW_MODE") or "").strip().casefold() == "true"
    url = explicit or ("http://apply:8000/health" if isolated else "")
    if not url:
        return {"configured": False, "reachable": False}
    try:
        response = httpx.get(url, timeout=1.25, follow_redirects=False)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {"configured": True, "reachable": False}
    return {
        "configured": True,
        "reachable": payload.get("status") == "healthy"
        and payload.get("database") == "healthy",
        "schema_version": payload.get("schema_version"),
        "migration_count": payload.get("migration_count"),
    }


def _card(title: str, value: str, detail: str) -> None:
    st.markdown(
        f"""<div class="settings-v22-card">
        <b>{_PAGES.esc(title)}</b>
        <span><strong>{_PAGES.esc(value)}</strong></span>
        <span>{_PAGES.esc(detail)}</span>
        </div>""",
        unsafe_allow_html=True,
    )


def _auth_status(preferences: dict[str, Any]) -> tuple[str, str]:
    if preferences["auth_mode"] == "subscription":
        return (
            "Subscription selected",
            "Claude Code login is verified separately by the MUNSHI Apply executor.",
        )
    if _vault_secret_exists(_ANTHROPIC_CREDENTIAL_TYPE):
        return (
            "API key stored",
            "Encrypted in the dashboard vault; executor readiness is verified separately.",
        )
    return ("Needs credential", "No encrypted Anthropic API key is stored.")


def _autoapply_status(preferences: dict[str, Any]) -> None:
    runtime = _runtime_health()
    auth_value, auth_detail = _auth_status(preferences)
    columns = st.columns(4, gap="small")
    with columns[0]:
        _card(
            "AutoApply",
            "Requested" if preferences["enabled"] else "Not requested",
            "Saved dashboard preference only. It is not standing submission consent.",
        )
    with columns[1]:
        _card(
            "Apply runtime",
            "Healthy" if runtime.get("reachable") else "Not confirmed",
            str(runtime.get("schema_version") or "Runtime health not currently confirmed"),
        )
    with columns[2]:
        _card("Claude authentication", auth_value, auth_detail)
    with columns[3]:
        _card(
            "Final submit",
            "Preference saved" if preferences["allow_final_submit"] else "Not requested",
            "No final submit without an exact frozen review and one-use authorization.",
        )


def _autoapply_form() -> None:
    preferences = _preferences()
    st.markdown("### Autonomous Apply")
    st.markdown(
        """<div class="settings-v22-copy">
        Configure the browser application executor here. This does not bypass
        Candidate Truth, Application Plan, security checkpoints, review digests,
        provider policy, or independent submission verification.
        </div>""",
        unsafe_allow_html=True,
    )
    _autoapply_status(preferences)

    with st.form("product_v23_autoapply_preferences"):
        enabled = st.toggle(
            "Enable autonomous browser executor",
            value=preferences["enabled"],
        )
        left, right = st.columns(2, gap="medium")
        with left:
            auth_mode = st.selectbox(
                "Claude authentication",
                list(_AUTH_MODES),
                index=list(_AUTH_MODES).index(preferences["auth_mode"]),
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
            allow_final_submit = st.toggle(
                "Permit final submit when the Application Plan also authorizes it",
                value=preferences["allow_final_submit"],
                help="Runtime submit authority remains a separate gate.",
            )
        if st.form_submit_button(
            "Save AutoApply settings",
            type="primary",
            use_container_width=True,
        ):
            _save_preferences(
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
            st.success("AutoApply preferences saved. Runtime safety gates are unchanged.")
            st.rerun()


def _native_standing_consent_form() -> None:
    """Explicit, bounded consent; the general AutoApply toggle is not authority."""
    from app import native_standing_consent_v1 as standing

    st.markdown("### Bounded Native standing consent")
    state = standing.status()
    st.caption(
        "Optional automatic approval only for freshly prepared Native jobs "
        "from exact named employers. It does not bypass Candidate Truth, "
        "protected checkpoints, the frozen review, or verified receipts."
    )
    if state.get("enabled"):
        st.info(
            f"Active until {state['expires_at']}. "
            f"Employers: {', '.join(state['companies'])}. "
            f"Match score ≥ {state['min_score']}%. "
            f"Limit {state['daily_cap']} applications per UTC day."
        )
        if st.button("Revoke future automatic approvals",
                     key="native_standing_consent_revoke"):
            standing.revoke()
            st.success("Future approvals disabled. Existing mandates require separate cancellation.")
            st.rerun()
        return

    with st.form("native_standing_consent_v1_form"):
        names = st.text_area(
            "Exact employer names, one per line (up to eight)",
            value="", height=80,
        )
        score = st.number_input("Minimum verified match score",
                                min_value=95,max_value=100,value=95,step=1)
        daily_cap = st.number_input("Maximum applications per day",
                                    min_value=1,max_value=3,value=1,step=1)
        hours = st.number_input("Consent duration in hours",
                                min_value=1,max_value=168,value=24,step=1)
        phrase = st.text_input(
            "Type ENABLE BOUNDED NATIVE AUTO APPLY to authorize",
            value="",autocomplete="off",
        )
        st.warning(
            "Enabling this grants approval for future matching applications "
            "without another package click. CAPTCHA, MFA, unresolved identity "
            "and unverified employer receipt remain blocked."
        )
        if st.form_submit_button("Enable bounded automatic approvals",
                                 type="primary",use_container_width=True):
            try:
                standing.grant(
                    company_names=[v.strip() for v in names.splitlines() if v.strip()],
                    score=int(score),daily_cap=int(daily_cap),
                    hours=int(hours),confirmation=phrase,
                )
            except (PermissionError, ValueError, RuntimeError) as error:
                st.error(str(error))
            else:
                st.success("Bounded consent saved for the current owner.")
                st.rerun()


def _anthropic_credential() -> None:
    preferences = _preferences()
    available = vault_available()
    configured = _vault_secret_exists(_ANTHROPIC_CREDENTIAL_TYPE)
    st.markdown("### Claude credential")

    if preferences["auth_mode"] == "subscription":
        st.info(
            "Subscription mode needs no Anthropic API key in this website. "
            "The paired MUNSHI Apply executor must have an authenticated Claude Code session."
        )
        return
    if not available:
        st.warning(
            "API-key mode is selected, but encrypted credential storage is not "
            "configured on this server. The credential field stays unavailable "
            "until the vault is configured."
        )
        return

    st.success(
        "Anthropic API credential is stored encrypted."
        if configured
        else "Encrypted storage is ready for an Anthropic API credential."
    )
    with st.form("product_v23_anthropic_credential"):
        secret = st.text_input(
            "Anthropic API key",
            type="password",
            placeholder="Stored value is never displayed",
            help=(
                "Saving replaces the encrypted credential. Plaintext is never "
                "read back into this page."
            ),
        )
        if st.form_submit_button(
            "Replace encrypted API key" if configured else "Store encrypted API key",
            type="primary",
            use_container_width=True,
        ):
            if not secret.strip():
                st.error("Enter an API key before saving.")
            else:
                try:
                    store_secret(_ANTHROPIC_CREDENTIAL_TYPE, secret.strip())
                except VaultError as error:
                    st.error(str(error))
                else:
                    st.success(
                        "Anthropic API credential stored without displaying the saved value."
                    )
                    st.rerun()

    if configured and st.button(
        "Delete stored Anthropic API credential",
        key="product_v23_delete_anthropic",
        use_container_width=True,
    ):
        delete_secret(_ANTHROPIC_CREDENTIAL_TYPE)
        st.success("Stored Anthropic API credential deleted.")
        st.rerun()


def _security_checkpoint_card() -> None:
    st.markdown("### Security checkpoint continuation")
    st.markdown(
        """<div class="settings-v22-card">
        <b>User-assisted verification · automatic continuation</b>
        <span>If CAPTCHA, MFA, OTP, identity, or another protected verification
        step appears, MUNSHI pauses and checkpoints the exact Chromium session.
        The website popup focuses that tab and resumes only after the extension
        re-scans the page and confirms the checkpoint is gone.</span>
        </div>""",
        unsafe_allow_html=True,
    )
    st.caption(
        "Passwords, verification codes, cookies, and challenge answers are never "
        "copied into this Settings page."
    )


def _credentials() -> None:
    st.markdown("### Credentials & AutoApply")
    if vault_available():
        st.success("Encrypted credential storage is available on this server.")
    else:
        st.warning(
            "Encrypted credential storage is not configured on this server. "
            "Subscription mode remains selectable; API-key entry stays unavailable."
        )
    st.markdown(
        """<div class="settings-v22-card">
        <b>Credential policy</b>
        <span>Passwords and API keys are never displayed in plaintext.
        Credential readiness and application authority are separate states.</span>
        </div>""",
        unsafe_allow_html=True,
    )
    _autoapply_form()
    _native_standing_consent_form()
    _anthropic_credential()
    _security_checkpoint_card()


def settings_v23() -> None:
    _PAGES.page_intro(
        "PREFERENCES",
        "Settings",
        "Readable product controls first. AutoApply configuration and credential "
        "readiness are visible here; engineering diagnostics remain under "
        "Advanced / System.",
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
            preferences = _preferences()
            st.markdown("### Review and submission authority")
            st.markdown(
                """<div class="settings-v22-card">
                <b>What this controls</b>
                <span>Review preference is separate from targeting, work
                authorization, dedupe, ATS, Application Plan, provider safety,
                and independent verification.</span>
                </div>""",
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
            _card(
                "Final submit preference",
                "Requested" if preferences["allow_final_submit"] else "Off",
                "Change this under Credentials & AutoApply. "
                "Runtime submit authority remains a separate gate.",
            )

        elif section == "Automation":
            policy = _PAGES.volume_policy()
            lanes = _PAGES.lanes()
            preferences = _preferences()
            st.markdown("### Automation")
            _card(
                "Discovery / preparation volume",
                policy["mode"].replace("_", " ").title(),
                f"{sum(bool(lane['enabled']) for lane in lanes)} of "
                f"{len(lanes)} target lane(s) enabled.",
            )
            _card(
                "Autonomous browser executor",
                "Enabled" if preferences["enabled"] else "Disabled",
                f"{preferences['model'].title()} · max {preferences['max_turns']} "
                f"turns · ${preferences['max_cost_per_application_usd']:.2f} "
                "ceiling per application",
            )
            st.markdown(
                '<a class="product-nav-link" '
                'href="?view=settings&amp;section=credentials" '
                'target="_self">Configure AutoApply →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Integrations":
            extension = _extension_profile_record()
            runtime = _runtime_health()
            st.markdown("### Integrations")
            _card(
                "MUNSHI Apply runtime",
                "Healthy" if runtime.get("reachable") else "Not confirmed",
                str(runtime.get("schema_version") or "No runtime schema reported"),
            )
            _card(
                "MUNSHI Apply profile bridge",
                "Connected" if extension else "Local only",
                (
                    "Server-side MasterProfile data is available."
                    if extension
                    else "The browser extension keeps MasterProfile locally until "
                    "a secure sync bridge is enabled."
                ),
            )
            _PAGES._inbox()

        elif section == "Profile & defaults":
            facts = _PAGES.candidate_facts()
            designated = _PAGES.master_resume()
            st.markdown("### Profile & defaults")
            _card(
                "Candidate profile",
                f"{len(facts)} structured fact(s)",
                f"Master resume: {'configured' if designated else 'not configured'}.",
            )
            st.markdown(
                '<a class="product-nav-link" '
                'href="?view=profile&amp;tab=details" '
                'target="_self">Open Profile details →</a>',
                unsafe_allow_html=True,
            )

        elif section == "Credentials":
            _credentials()

        elif section == "Advanced / System":
            advanced_v22()
            # First-class product controls promoted from staging. Keep them
            # direct in the canonical Settings renderer so Streamlit reruns
            # cannot accumulate duplicate wrapper/form layers.
            from app.targeting_settings_ui_v1 import render_targeting_settings
            from app.application_notification_ui_v1 import (
                render_application_notification_settings,
            )

            render_targeting_settings()
            render_application_notification_settings()


def install_autoapply_settings_ui(product_pages: Any) -> None:
    """Install the Settings replacement after the established V2.2 layer."""
    global _PAGES
    _PAGES = product_pages
    product_pages.settings = settings_v23
