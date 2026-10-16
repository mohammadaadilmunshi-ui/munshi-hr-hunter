"""Application provider selection and status inside MUNSHI's original dashboard.

This is presentation-only until a candidate explicitly saves one provider
ownership record for an eligible, fully verified preflight package.  No MCP
client, background worker, submit operation, or provider credentials are
available through this module.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from app.linkedin_employer_link_resolver_v1 import external_url
from app.linkedin_verified_url_evidence_v5 import latest_verified_for_job

PROVIDER_LABELS = {"NATIVE": "MUNSHI Native", "TSENTA": "Tsenta"}
STATUS_LABELS = {
    "PREPARATION": "Package selected",
    "FILLING": "Completing application",
    "PAUSED": "Needs attention",
    "FAILED": "Stopped safely",
    "PROVIDER_SUBMITTED": "Provider reports submitted",
    "EMPLOYER_CONFIRMED": "Employer confirmation verified",
}


def verified_apply_destination(job: Mapping[str, Any], connection: Any) -> str | None:
    """Show only exact signed LinkedIn employer proof or canonical ATS URLs."""
    if "linkedin" in str(job.get("source") or "").casefold():
        evidence = latest_verified_for_job(connection, job)
        return external_url((evidence or {}).get("employer_apply_url"))
    return external_url(job.get("apply_url")) or external_url(job.get("job_url"))


def provider_readiness(
    *,
    job: Mapping[str, Any],
    flow: Mapping[str, Any],
    employer_apply_url: str | None,
    tsenta_backend_connected: bool = False,
) -> dict[str, Any]:
    """Plan-only UI state. The router enforces the authoritative rules again."""
    current = flow.get("provider_route")
    if isinstance(current, Mapping) and current:
        return {
            "route_exists": True,
            "route": dict(current),
            "can_select_native": False,
            "can_select_tsenta": False,
            "reason": "This opening already has an execution provider.",
            "package_id": None,
        }

    artifacts = flow.get("artifacts") or {}
    preflight = artifacts.get("preflight") or {}
    package_id = str(preflight.get("preflight_package_id") or "").strip()
    package_ready = preflight.get("preflight_status") == "READY_TO_APPLY"
    completed_or_ambiguous = str(flow.get("state") or "") in {
        "SUBMITTED", "VERIFIED", "AWAITING_RESPONSE",
        "SUBMISSION_UNVERIFIED", "ASSESSMENT", "INTERVIEW",
        "OFFER", "REJECTED", "WITHDRAWN",
    }
    # Match the authoritative provider router's opt/work-authorization traps,
    # so the screen cannot show an action that the backend must later refuse.
    try:
        already_applied = bool(int(job.get("already_applied") or 0))
        cpt_trapdoor = bool(int(job.get("cpt_trapdoor") or 0))
    except (TypeError, ValueError, OverflowError):
        already_applied, cpt_trapdoor = True, True
    eligible = (
        not completed_or_ambiguous
        and str(job.get("status") or "found") == "found"
        and not job.get("hard_rejection_reason")
        and not already_applied
        and not cpt_trapdoor
        and str(job.get("primary_decision") or "") == "ELIGIBLE"
    )
    signed_destination = bool(external_url(employer_apply_url))
    if not eligible:
        reason = "Job eligibility must be confirmed before choosing an engine."
    elif not signed_destination:
        reason = "Official employer application URL has not been verified."
    elif not package_id or not package_ready:
        reason = "Complete the application package before selecting an engine."
    else:
        reason = "Selection reserves one engine; it does not start an application."

    can_native = bool(eligible and signed_destination and package_id and package_ready)
    return {
        "route_exists": False,
        "route": None,
        "can_select_native": can_native,
        "can_select_tsenta": can_native and tsenta_backend_connected,
        "reason": reason,
        "package_id": package_id or None,
    }


def provider_progress(route: Mapping[str, Any] | None) -> dict[str, Any]:
    if not route:
        return {
            "provider": "Not selected",
            "status": "Choose an engine after preparing the application",
            "receipt": "Not recorded",
            "is_employer_confirmed": False,
        }
    provider = str(route.get("provider") or "")
    state = str(route.get("state") or "")
    return {
        "provider": PROVIDER_LABELS.get(provider, "Execution provider"),
        "status": STATUS_LABELS.get(state, "Status unavailable"),
        "receipt": (
            "Independently verified"
            if state == "EMPLOYER_CONFIRMED" and route.get("receipt_evidence_digest")
            else "Not independently verified"
        ),
        "is_employer_confirmed": (
            state == "EMPLOYER_CONFIRMED" and bool(route.get("receipt_evidence_digest"))
        ),
    }


def learning_summary() -> dict[str, int]:
    """Read only sanitized observation totals from the authenticated owner scope."""
    from app.database import get_connection
    from app.tenant_foundation import current_owner_readonly

    connection = get_connection()
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='tsenta_observations_v1'"
        ).fetchone()
        if exists is None:
            return {"form_fields": 0, "provider_events": 0}
        owner = current_owner_readonly(connection)
        rows = connection.execute(
            """SELECT observation_kind,COUNT(*) AS count
                 FROM tsenta_observations_v1
                WHERE tenant_id=? AND user_id=?
                GROUP BY observation_kind""",
            (owner.tenant_id, owner.user_id),
        ).fetchall()
        counts = {str(row["observation_kind"]): int(row["count"]) for row in rows}
        return {
            "form_fields": counts.get("FORM_FIELD", 0),
            "provider_events": counts.get("APPLICATION_STATUS", 0),
        }
    finally:
        connection.close()


def render_connection_panel() -> None:
    """Explicit OAuth actions; never auto-connect or copy a local Codex grant."""
    import os

    import streamlit as st

    from app import tsenta_oauth_v1

    with st.expander("Tsenta account connection", expanded=False):
        try:
            current = tsenta_oauth_v1.status()
        except (sqlite3.OperationalError, RuntimeError, ValueError, OSError):
            current = {"authorized": False, "profile_read_verified": False}
        authorized = current.get("authorized") is True
        verified = current.get("profile_read_verified") is True
        if not authorized:
            st.caption("No Netcup Tsenta account has been connected.")
            ingress = (
                str(os.getenv(tsenta_oauth_v1.INGRESS_ENV) or "").lower()
                in {"true", "1", "yes"}
            )
            if not ingress:
                st.info(
                    "Connect is unavailable until the dashboard HTTPS callback "
                    "is verified in staging."
                )
            elif st.button(
                "Connect Tsenta securely",
                key="tsenta_oauth_connect",
                use_container_width=True,
            ):
                try:
                    intent = tsenta_oauth_v1.begin()
                    st.session_state["_tsenta_oauth_authorization_url"] = (
                        intent["authorization_url"]
                    )
                except (RuntimeError, ValueError):
                    st.error(
                        "Tsenta authorization could not start safely. "
                        "Review the connection configuration."
                    )
            pending = st.session_state.get("_tsenta_oauth_authorization_url")
            if pending and ingress:
                st.link_button(
                    "Continue to Tsenta authorization",
                    pending,
                    use_container_width=True,
                )
                st.caption("Approve access on Tsenta; you return to this dashboard.")
        else:
            st.success(
                "Backend profile verified."
                if verified
                else "OAuth connected. Verify profile access to finish setup."
            )
            if not verified and st.button(
                "Verify Tsenta profile access",
                key="tsenta_oauth_verify",
                use_container_width=True,
            ):
                try:
                    tsenta_oauth_v1.verify_profile()
                except (RuntimeError, ValueError):
                    st.error(
                        "The Tsenta account could not be verified. "
                        "No provider application was started."
                    )
                else:
                    st.session_state.pop("_tsenta_oauth_authorization_url", None)
                    st.rerun()
            st.caption(
                "OAuth connection alone does not enable application submission. "
                "The verified execution worker remains a separate release gate."
            )
            approved = st.checkbox(
                "Disconnect this local MUNSHI account connection",
                key="tsenta_oauth_disconnect_confirm",
            )
            if st.button(
                "Disconnect",
                key="tsenta_oauth_disconnect",
                disabled=not approved,
                use_container_width=True,
            ):
                tsenta_oauth_v1.disconnect()
                st.session_state.pop("_tsenta_oauth_authorization_url", None)
                st.info(
                    "Local credentials disconnected. Review Tsenta account "
                    "settings separately to revoke provider-side access."
                )
                st.rerun()


def render_dashboard_summary() -> None:
    """Compact operator status on the original dashboard, with no account reads."""
    import streamlit as st

    with st.container(border=True):
        st.markdown("#### Application engines")
        native, tsenta, signals = st.columns(3, gap="small")
        native.markdown("**MUNSHI Native**")
        native.caption("Existing preparation engine")
        tsenta.markdown("**Tsenta**")
        from app import tsenta_backend_readiness_v1

        connection = tsenta_backend_readiness_v1.connection_status()
        if connection["profile_read_verified"]:
            tsenta.caption("Profile verified · execution pending")
        elif connection["authenticated_backend"]:
            tsenta.caption("Connected · verification pending")
        else:
            tsenta.caption("Backend connection required")
        try:
            totals = learning_summary()
        except (sqlite3.OperationalError, LookupError, PermissionError, RuntimeError):
            totals = {"form_fields": 0, "provider_events": 0}
        signals.metric("Learning signals", sum(totals.values()))
        st.caption(
            "Engine selection is in Applications. "
            "No engine is authorized to submit from this panel."
        )
        render_connection_panel()


def render_workspace(job_id: int) -> None:
    """An embedded panel in the original Applications workspace."""
    import streamlit as st

    from app import application_provider_router_v1 as router
    from app import product_application_flow_v2, tsenta_backend_readiness_v1
    from app.database import get_connection

    flow = product_application_flow_v2.flow_for_job(int(job_id), summary=True)
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        if row is None:
            return
        job = dict(row)
        try:
            employer_url = verified_apply_destination(job, connection)
        except (sqlite3.OperationalError, LookupError, RuntimeError, ValueError):
            # Missing/unmigrated evidence must fail closed.
            employer_url = None
    finally:
        connection.close()

    # Never infer connectivity from a Mac Codex session or env flag.
    backend_status = tsenta_backend_readiness_v1.connection_status()
    state = provider_readiness(
        job=job,
        flow=flow,
        employer_apply_url=employer_url,
        tsenta_backend_connected=backend_status["execution_ready"] is True,
    )
    progress = provider_progress(state["route"])
    with st.container(border=True):
        st.markdown("### Application engine")
        st.caption(
            "The dashboard owns the application. Selecting an engine reserves "
            "this opening; submitting requires a separate authorized execution."
        )
        metrics = st.columns(2, gap="small")
        metrics[0].metric("Engine", progress["provider"])
        metrics[1].metric("Employer receipt", progress["receipt"])

        if state["route_exists"]:
            if progress["is_employer_confirmed"]:
                st.success(progress["status"])
            else:
                st.info(progress["status"])
            with st.expander("Execution details", expanded=False):
                route = state["route"] or {}
                st.write("State:", str(route.get("state") or "Unknown"))
                st.write("Provider:", progress["provider"])
                st.write("Attempts:", int(route.get("attempt_count") or 0))
                st.caption(
                    "A provider-reported submission is not an employer receipt. "
                    "Application IDs and private answers are intentionally hidden."
                )
            return

        st.caption(state["reason"])
        st.caption("Tsenta: backend connection not configured. Local Codex OAuth is not server access.")
        provider = st.radio(
            "Execution provider",
            options=("NATIVE", "TSENTA"),
            format_func=lambda value: PROVIDER_LABELS[value],
            horizontal=True,
            key=f"application_provider_choice_{int(job_id)}",
        )
        if provider == "TSENTA":
            st.warning(
                "Tsenta execution is unavailable until an authorized backend "
                "account connection and staged verification are completed."
            )
        else:
            st.caption("MUNSHI Native uses the existing hosted Apply preparation pipeline.")

        allowed = (
            state["can_select_native"] if provider == "NATIVE"
            else state["can_select_tsenta"]
        )
        confirmed = st.checkbox(
            "Reserve this opening for the selected engine",
            key=f"application_provider_confirm_{int(job_id)}",
            disabled=not allowed,
        )
        if st.button(
            "Save engine selection",
            key=f"application_provider_save_{int(job_id)}",
            type="primary",
            disabled=not (allowed and confirmed),
            use_container_width=True,
        ):
            try:
                # This is the only durable mutation in this view; execution remains
                # disabled and the router revalidates exact-job signed evidence.
                router.select_provider(
                    job_id=int(job_id),
                    provider=provider,
                    verified_employer_url=str(employer_url),
                    preflight_package_id=str(state["package_id"]),
                    idempotency_key=f"ui-engine-reservation-{int(job_id)}",
                )
            except (ValueError, LookupError, FileExistsError, PermissionError, RuntimeError):
                st.error("Engine selection was not saved. Review application eligibility and package status.")
            else:
                st.success("Engine reserved. No application was submitted.")
                st.rerun()
