"""Initial-dashboard runtime hotfixes that are safe inside Streamlit only.

This module deliberately does not alter API/worker migration authority. The
Hunter supervisor starts FastAPI first and waits for its health endpoint before
starting Streamlit, so the API remains responsible for authoritative schema
initialization. Streamlit then uses read-first schema guards: if the expected
schema already exists, ordinary page renders avoid CREATE TABLE/INDEX work. If
a table is genuinely absent (for example, a standalone development launch), the
original schema initializer is called as a compatibility fallback.

The module also repairs the Settings Advanced/System renderer so it dynamically
uses the latest wrapped V2.2 renderer. This keeps the provider telemetry window
visible after AutoApply Settings replaces the Settings page.
"""
from __future__ import annotations

import functools
import sqlite3
from typing import Any, Callable, Iterable

VERSION = "initial-dashboard-runtime-hotfix-v1.1"
MARKER = "AADIL_INITIAL_DASHBOARD_RUNTIME_HOTFIX_V1_1"


SchemaReadyProbe = Callable[[sqlite3.Connection], bool]
ConnectionFactory = Callable[[], sqlite3.Connection]


def _tables_ready(connection: sqlite3.Connection, required: Iterable[str]) -> bool:
    names = tuple(dict.fromkeys(str(name) for name in required if str(name)))
    if not names:
        return True
    placeholders = ",".join("?" for _ in names)
    rows = connection.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        names,
    ).fetchall()
    present = {str(row[0]) for row in rows}
    return set(names).issubset(present)


def _tenant_ready(connection: sqlite3.Connection) -> bool:
    if not _tables_ready(
        connection,
        ("tenants", "app_users", "tenant_memberships", "owned_record_owners"),
    ):
        return False
    row = connection.execute(
        "SELECT 1 FROM tenant_memberships WHERE tenant_id='default' AND user_id='local-owner' LIMIT 1"
    ).fetchone()
    return row is not None


def _install_read_first_guard(
    module: Any,
    *,
    required_tables: Iterable[str],
    ready_probe: SchemaReadyProbe | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> bool:
    """Wrap ``module.ensure_schema`` so existing schemas stay read-only on render."""
    original = getattr(module, "ensure_schema", None)
    get_connection = connection_factory or getattr(module, "get_connection", None)
    if not callable(original) or not callable(get_connection):
        return False
    if getattr(original, "_aadil_read_first_schema_guard", False):
        return True

    required = tuple(required_tables)

    @functools.wraps(original)
    def guarded(connection: sqlite3.Connection | None = None) -> None:
        owns = connection is None
        active = connection or get_connection()
        try:
            try:
                ready = (
                    ready_probe(active)
                    if ready_probe is not None
                    else _tables_ready(active, required)
                )
            except sqlite3.Error:
                ready = False

            if ready:
                return

            # Compatibility fallback only. In the production supervisor this
            # should not execute because FastAPI initializes the schema before
            # Streamlit starts.
            original(active)
            if owns and active.in_transaction:
                active.commit()
        finally:
            if owns:
                active.close()

    guarded._aadil_read_first_schema_guard = True  # type: ignore[attr-defined]
    guarded._aadil_original_ensure_schema = original  # type: ignore[attr-defined]
    setattr(module, "ensure_schema", guarded)
    return True


def _install_dynamic_advanced_renderer() -> bool:
    """Keep Settings -> Advanced/System bound to the latest runtime wrapper."""
    from app import autoapply_settings_ui

    current = getattr(autoapply_settings_ui, "advanced_v22", None)
    if getattr(current, "_aadil_dynamic_advanced_renderer", False):
        return True

    def dynamic_advanced_v22() -> None:
        # Resolve at call time. provider_telemetry_window_v1 intentionally wraps
        # product_v22.advanced_v22 later in product_shell.render().
        from app import product_v22

        product_v22.advanced_v22()

    dynamic_advanced_v22._aadil_dynamic_advanced_renderer = True  # type: ignore[attr-defined]
    dynamic_advanced_v22._aadil_prior_advanced_renderer = current  # type: ignore[attr-defined]
    autoapply_settings_ui.advanced_v22 = dynamic_advanced_v22
    return True


def install() -> dict[str, Any]:
    """Install Streamlit-only read-first schema guards and UI renderer repair."""
    from app import answer_brain
    from app import candidate_artifacts
    from app import candidate_digital_twin
    from app import native_resume_service
    from app import native_resume_service_v2
    from app import native_resume_service_v3
    from app import native_resume_service_v4
    from app import phase4_job_binding
    from app import phase45_truth_binding
    from app import product_state
    from app import secure_vault
    from app import tenant_foundation

    db_factory = native_resume_service.get_connection

    guards = {
        "tenant_foundation": _install_read_first_guard(
            tenant_foundation,
            required_tables=(
                "tenants",
                "app_users",
                "tenant_memberships",
                "owned_record_owners",
            ),
            ready_probe=_tenant_ready,
        ),
        "product_state": _install_read_first_guard(
            product_state,
            required_tables=(
                "product_job_state",
                "auto_prepare_lanes",
                "candidate_profile_facts",
                "gmail_messages",
            ),
        ),
        "candidate_artifacts": _install_read_first_guard(
            candidate_artifacts,
            required_tables=(
                "candidate_artifacts",
                "candidate_artifact_designations",
            ),
        ),
        "candidate_digital_twin": _install_read_first_guard(
            candidate_digital_twin,
            required_tables=(
                "candidate_digital_twin_facts",
                "candidate_digital_twin_evidence",
                "candidate_digital_twin_preferences",
                "candidate_digital_twin_onboarding",
            ),
        ),
        "answer_brain": _install_read_first_guard(
            answer_brain,
            required_tables=(
                "application_answer_vault",
                "sensitive_self_identification_vault",
            ),
        ),
        "secure_vault": _install_read_first_guard(
            secure_vault,
            required_tables=("credential_secret", "credential_secret_audit"),
        ),
        "native_resume_service": _install_read_first_guard(
            native_resume_service,
            required_tables=("native_resume_sources", "native_resume_versions"),
            connection_factory=db_factory,
        ),
        "native_resume_service_v2": _install_read_first_guard(
            native_resume_service_v2,
            required_tables=(
                "native_resume_sources",
                "native_resume_versions",
                "native_resume_writer_settings",
                "credential_secret",
                "credential_secret_audit",
            ),
            connection_factory=db_factory,
        ),
        "native_resume_service_v3": _install_read_first_guard(
            native_resume_service_v3,
            required_tables=(
                "native_resume_sources",
                "native_resume_versions",
                "native_resume_writer_settings",
                "native_resume_profile_extracts",
            ),
            connection_factory=db_factory,
        ),
        "phase45_truth_binding": _install_read_first_guard(
            phase45_truth_binding,
            required_tables=(
                "native_resume_versions",
                "application_answer_vault",
                "native_resume_truth_bindings",
                "application_answer_truth_bindings",
            ),
        ),
        "phase4_job_binding": _install_read_first_guard(
            phase4_job_binding,
            required_tables=(
                "native_resume_versions",
                "native_resume_job_bindings",
            ),
        ),
        "native_resume_service_v4": _install_read_first_guard(
            native_resume_service_v4,
            required_tables=(
                "native_resume_versions",
                "native_resume_truth_bindings",
                "application_answer_truth_bindings",
                "native_resume_job_bindings",
            ),
            connection_factory=db_factory,
        ),
    }

    advanced = _install_dynamic_advanced_renderer()
    return {
        "success": all(guards.values()) and advanced,
        "version": VERSION,
        "marker": MARKER,
        "schema_guards": guards,
        "dynamic_advanced_renderer": advanced,
    }
