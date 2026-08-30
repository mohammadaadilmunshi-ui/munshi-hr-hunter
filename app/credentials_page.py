from __future__ import annotations

import streamlit as st

from app.ui_time import format_local, timezone_label

MARKER = "AADIL_USAJOBS_CREDENTIALS_PAGE_V19"


def usajobs_status_labels(
    *, configured: bool, runtime_enabled: bool, connection_verified: bool
) -> dict[str, str]:
    return {
        "credentials": "Configured" if configured else "Not configured",
        "connection": "Verified" if connection_verified else ("Ready to test" if configured else "Not ready"),
        "runtime": "Enabled" if runtime_enabled else "Disabled",
    }


def render_credentials_page() -> None:
    from app.secure_credentials import (
        CredentialError,
        credential_status,
        delete_credentials,
        save_credentials,
        test_credentials,
    )

    st.markdown(
        """
        <div class="page-intro">
          <div><div class="page-kicker">Secure integrations</div><h2>Credentials</h2>
          <div class="page-copy">Manage local integration identity without exposing secrets or changing runtime enablement.</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        status = credential_status()
    except Exception as exc:
        st.error(f"Credential status unavailable: {type(exc).__name__}: {exc}")
        return

    from app.database import get_connection

    connection = get_connection()
    try:
        row = connection.execute(
            """
            SELECT enabled, health_status, schedule_state, cadence_minutes,
                   last_http_status, last_success_at, last_run_at
            FROM source_runtime_truth_v1 WHERE source_name='USAJobs'
            """
        ).fetchone()
    finally:
        connection.close()
    source_enabled = bool(row["enabled"]) if row else False
    configured = bool(status.get("api_key_present") and status.get("email_present"))
    connection_verified = bool(
        configured
        and row
        and (int(row["last_http_status"] or 0) == 200 or bool(row["last_success_at"]))
    )
    labels = usajobs_status_labels(
        configured=configured,
        runtime_enabled=source_enabled,
        connection_verified=connection_verified,
    )
    st.markdown(
        f"""
        <div class="adapter-truth-panel">
          <div><div class="integration-name">USAJobs</div><div class="integration-sub">Official U.S. Government API · secure local integration</div></div>
          <div class="status-grid">
            <div class="status-cell"><span>Credentials</span><strong>{labels['credentials']}</strong></div>
            <div class="status-cell"><span>Stored key</span><strong>{status.get('api_key_masked') or 'Not configured'}</strong></div>
            <div class="status-cell"><span>Storage</span><strong>{status.get('api_key_source') or 'Not available'}</strong></div>
            <div class="status-cell"><span>API connection</span><strong>{labels['connection']}</strong></div>
            <div class="status-cell"><span>Runtime</span><strong>{labels['runtime']}</strong></div>
            <div class="status-cell"><span>Scheduler</span><strong>{str(row['schedule_state'] or 'disabled').replace('_', ' ').title() if row else 'Not available'}</strong></div>
            <div class="status-cell"><span>Cadence</span><strong>{f"{int(row['cadence_minutes'])} minutes" if row else 'Not available'}</strong></div>
            <div class="status-cell"><span>Last run</span><strong>{format_local(row['last_run_at'], empty='Never run') if row else 'Never run'}</strong></div>
            <div class="status-cell"><span>Enablement authority</span><strong>Source Health</strong></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.caption(f"The full API key is never displayed. Source policy and credential readiness are separate. Times shown in system local time · {timezone_label()}.")

    with st.expander("Add or replace USAJobs credentials", expanded=not configured):
        with st.form("usajobs_credentials_v19", clear_on_submit=True):
            email = st.text_input(
                "USAJobs email / User-Agent",
                value=str(status.get("email") or ""),
                help="Use the same email address used in your USAJOBS API access request.",
            )
            api_key = st.text_input(
                "USAJobs API key",
                type="password",
                value="",
                help="Leave blank to keep the currently stored API key.",
            )
            save_clicked = st.form_submit_button(
                "Save credentials securely",
                type="primary",
            )

    if save_clicked:
        try:
            save_credentials(api_key=api_key or None, email=email)
            st.success(
                "USAJobs credentials saved securely in macOS Keychain. "
                "Run the credential test before enabling the source."
            )
            st.rerun()
        except CredentialError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(f"Save failed: {type(exc).__name__}: {exc}")

    st.markdown("#### Connection verification")
    st.caption("Testing makes one read-only request to the official USAJOBS Search API. It does not enable the source, schedule a run, or store returned jobs.")
    if st.button("Test official USAJobs connection", width="stretch"):
        with st.spinner("Testing the official USAJOBS Search API…"):
            result = test_credentials()
        if result.get("ok"):
            st.success(
                f"{result.get('message')} HTTP {result.get('http_status')}; "
                f"returned {result.get('returned', 0)} of "
                f"{result.get('total_matches', 0)} matching records."
            )
        else:
            st.error(result.get("message") or "Credential verification failed.")

    with st.expander("Remove USAJobs credentials", expanded=False):
        st.warning("Removing credentials also forces USAJobs disabled through the existing canonical source-policy authority.")
        confirm_delete = st.checkbox(
            "I understand this removes the dashboard-managed USAJobs credentials",
            key="usajobs_delete_confirm_v19",
        )
        if st.button(
            "Delete credentials",
            disabled=not confirm_delete,
            width="stretch",
        ):
            try:
                from app.database import save_source_policy

                save_source_policy(
                    "USAJobs",
                    enabled=False,
                    changed_by="streamlit:credentials:v19",
                )
                delete_credentials()
                st.success(
                    "USAJobs credentials removed from macOS Keychain and USAJobs remains disabled."
                )
                st.rerun()
            except CredentialError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Delete failed: {type(exc).__name__}: {exc}")

    st.info(
        "Saving or testing credentials does not enable USAJobs. "
        "After verification succeeds, enable USAJobs deliberately from Source Health.",
        icon="🔐",
    )
