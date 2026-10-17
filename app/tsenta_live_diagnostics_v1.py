"""Explicit, read-only privacy-safe Tsenta submission diagnostic.

Never transmits MUNSHI alias, Candidate Truth, or a resume to Tsenta.
Never enables submission. A refresh token may be rotated by the OAuth layer.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

CUSTOM_RESUME_TOOLS = frozenset({
    "upload_custom_resume", "check_custom_resume",
    "apply_with_custom_resume",
})


def diagnose_submission_setup() -> dict[str, Any]:
    """Authorized status probe; call only after an explicit dashboard action."""
    from app import tsenta_oauth_v1
    from app.mailbox_verification_service import runtime_health
    from app.tsenta_mcp_http_v1 import TsentaMcpReadClient

    alias = None
    connection = get_connection()
    try:
        connection.execute("PRAGMA query_only=ON")
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT email_address FROM ats_application_identities
               WHERE tenant_id=? AND user_id=? AND relay_state='ACTIVE'
               ORDER BY datetime(updated_at) DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id),
        ).fetchone()
        alias = str(row["email_address"]).strip() if row else None
    finally:
        connection.close()

    health = runtime_health()
    token = tsenta_oauth_v1._token_for_current_owner()
    with TsentaMcpReadClient(token) as mcp:
        mcp.initialize()
        # The adapter parses MCP result envelopes and selects the same default
        # resume profile as the main account-connection UI.
        from app.tsenta_mcp_adapter_v1 import TsentaMcpAdapter

        adapter = TsentaMcpAdapter(
            lambda name, args: mcp.call_read(name, args),
            available_tools=list(mcp._tools),
        )
        snapshot = adapter.read_account_snapshot()
        catalog = mcp._send("tools/list", {})
    tools = set()
    for record in (catalog.get("tools") or []) if isinstance(catalog, Mapping) else []:
        if isinstance(record, Mapping):
            tools.add(str(record.get("name") or "").replace("-", "_"))
    data = snapshot.selected_resume_profile.get("data") or {}
    personal = data.get("personalInformation") if isinstance(data, Mapping) else {}
    stored_email = personal.get("email") if isinstance(personal, Mapping) else None
    match = bool(
        alias and isinstance(stored_email, str)
        and alias.casefold() == stored_email.strip().casefold()
    )
    found_tools = CUSTOM_RESUME_TOOLS.issubset(tools)
    verified = snapshot.privacy_safe_summary()["onboarding_complete"] is True
    blockers = []
    if not verified:
        blockers.append("TSENTA_ONBOARDING_INCOMPLETE")
    if not alias:
        blockers.append("MUNSHI_ACTIVE_APPLICATION_ALIAS_MISSING")
    if health.get("ready") is not True:
        blockers.append("MUNSHI_INBOX_VERIFICATION_UNREADY")
    if not found_tools:
        blockers.append("CUSTOM_RESUME_TOOL_INVENTORY_INCOMPLETE")
    if alias and not match:
        blockers.append("TSENTA_PROFILE_EMAIL_DIFFERS_FROM_MUNSHI_ALIAS")
    blockers.append("ONE_USE_DISPATCH_WORKER_NOT_VALIDATED")
    return {
        "profile_verified": verified,
        "managed_email_active": bool(alias),
        "managed_email_matches_tsenta": match,
        "custom_resume_tools_present": found_tools,
        "mailbox_ready": health.get("ready") is True,
        "execution_ready": False,
        "reason_codes": blockers,
        "raw_identity_in_response": False,
    }
