"""Read-only managed ATS account and application-email readiness UI.

This surface exposes operational account state without ever reading or rendering
credential plaintext, one-time verification artifacts, cookies, or submission
authority. Remote ATS account creation remains owned by the Apply browser runtime;
Hunter displays only durable state that has actually been recorded.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import ats_credentials
from app import ats_managed_identity
from app.database import get_connection
from app.integration_credentials_v1 import integration_readiness


_PROVIDER_ORDER = (
    "GREENHOUSE",
    "LEVER",
    "ASHBY",
    "SMARTRECRUITERS",
    "WORKDAY",
    "ICIMS",
    "ORACLE",
    "TALEO",
)


def _table_exists(connection: Any, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def readiness_snapshot() -> dict[str, Any]:
    """Return non-secret account/email readiness for the current tenant owner."""
    owner = ats_credentials.current_owner()
    identity: dict[str, Any] | None = None
    accounts: list[dict[str, Any]] = []
    managed_ids: set[str] = set()

    connection = get_connection()
    try:
        if _table_exists(connection, "ats_application_identities"):
            row = connection.execute(
                """
                SELECT email_address,relay_state,updated_at
                FROM ats_application_identities
                WHERE tenant_id=? AND user_id=?
                ORDER BY datetime(updated_at) DESC
                LIMIT 1
                """,
                (owner.tenant_id, owner.user_id),
            ).fetchone()
            if row is not None:
                identity = dict(row)

        if _table_exists(connection, "ats_managed_account_bindings"):
            managed_ids = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT account_id
                    FROM ats_managed_account_bindings
                    WHERE tenant_id=? AND user_id=?
                    """,
                    (owner.tenant_id, owner.user_id),
                ).fetchall()
            }

        if _table_exists(connection, "ats_credential_accounts"):
            rows = connection.execute(
                """
                SELECT account_id,provider,account_scope,label,state,
                       consented_at,updated_at
                FROM ats_credential_accounts
                WHERE tenant_id=? AND user_id=?
                ORDER BY datetime(updated_at) DESC, provider, account_scope
                """,
                (owner.tenant_id, owner.user_id),
            ).fetchall()
            accounts = [
                {
                    "account_id": str(row["account_id"]),
                    "provider": str(row["provider"]),
                    "account_scope": str(row["account_scope"]),
                    "label": str(row["label"]),
                    "state": str(row["state"]),
                    "consented_at": row["consented_at"],
                    "updated_at": row["updated_at"],
                    "password_managed": str(row["account_id"]) in managed_ids,
                }
                for row in rows
            ]
    finally:
        connection.close()

    integration = integration_readiness()
    mail = dict(integration.get("mail_router") or {})
    return {
        "mail_domain": ats_managed_identity.application_mail_domain(),
        "hunter_mail_relay_ready": ats_managed_identity.mail_relay_ready(),
        "external_mail_sync_confirmed": bool(mail.get("external_sync_confirmed")),
        "identity": identity,
        "accounts": accounts,
        "managed_account_count": len(managed_ids),
        "provider_policies": [
            {
                "provider": provider,
                "account_policy": ats_credentials.POLICIES.get(provider, "UNSUPPORTED"),
            }
            for provider in _PROVIDER_ORDER
        ],
    }


def render_ats_account_readiness() -> None:
    snapshot = readiness_snapshot()
    identity = dict(snapshot.get("identity") or {})
    accounts = list(snapshot.get("accounts") or [])

    st.divider()
    st.markdown("## ATS accounts & application email")
    st.caption(
        "Read-only readiness for provider accounts used by AutoApply. Passwords, "
        "verification codes, magic links, cookies and submit authority are never "
        "displayed on this page."
    )

    metrics = st.columns(4, gap="small")
    metrics[0].metric(
        "Application email",
        identity.get("email_address") or "Reserved on demand",
    )
    metrics[1].metric(
        "Hunter mail relay",
        "Ready" if snapshot["hunter_mail_relay_ready"] else "Not ready",
    )
    metrics[2].metric("ATS account records", len(accounts))
    metrics[3].metric("Managed passwords", snapshot["managed_account_count"])

    relay_state = str(identity.get("relay_state") or "NOT_RESERVED")
    if snapshot["hunter_mail_relay_ready"]:
        st.success(
            f"Hunter application-mail relay is enabled · identity state: {relay_state}."
        )
    else:
        st.warning(
            "Hunter application-mail relay is not enabled. Account verification "
            "that depends on inbound email cannot continue automatically."
        )

    if snapshot["external_mail_sync_confirmed"]:
        st.success("External mail-router synchronization is confirmed.")
    else:
        st.warning(
            "Cloudflare Worker / mail.munshi.systems delivery is not yet proven "
            "end to end. Hunter can receive signed mail, but external delivery "
            "must be completed before ATS email verification is treated as ready."
        )

    st.markdown("### Provider account policy")
    st.dataframe(
        [
            {
                "Provider": row["provider"].title().replace("Smartrecruiters", "SmartRecruiters"),
                "Account behavior": row["account_policy"].replace("_", " ").title(),
                "Remote creation": "Only after Apply browser confirmation",
            }
            for row in snapshot["provider_policies"]
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.markdown("### Recorded ATS accounts")
    if not accounts:
        st.info(
            "No provider account has been durably recorded yet. MUNSHI may prepare "
            "managed credentials when an application actually requires an account; "
            "this screen never claims a remote account exists until the runtime records it."
        )
        return

    st.dataframe(
        [
            {
                "Provider": row["provider"],
                "Scope": row["account_scope"],
                "State": row["state"].replace("_", " ").title(),
                "Managed password": "Yes" if row["password_managed"] else "No",
                "Updated": row["updated_at"],
            }
            for row in accounts
        ],
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "A local credential record is not proof that an employer-side account was "
        "created. Remote creation/verification status advances only from browser "
        "runtime evidence."
    )


def install_ats_account_readiness_ui(pages_module: Any) -> None:
    if getattr(pages_module, "_ats_account_readiness_ui_v1_installed", False):
        return
    original_settings = pages_module.settings

    def settings_with_ats_account_readiness() -> None:
        original_settings()
        if st.session_state.get("product_settings_section") == "Credentials":
            render_ats_account_readiness()

    pages_module.settings = settings_with_ats_account_readiness
    pages_module._ats_account_readiness_ui_v1_installed = True
