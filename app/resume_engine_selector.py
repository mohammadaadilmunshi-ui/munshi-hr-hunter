"""User choice between the n8n writer and the MUNSHI Native Resume engine.

The selector is preparation-only. Native Resume V5 uses the permanent Candidate
Truth Profile and exact stored job, generates immediately, materializes checked
DOCX/PDF artifacts, and then opens Resume Studio for review. Neither path owns
final application submission.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import streamlit as st

_PAGES: Any = None
_DIALOG_JOB_KEY = "resume_engine_choice_job_id"
_NATIVE_V5_ENV = "MUNSHI_NATIVE_RESUME_V5_ENABLED"
_DEFAULT_NATIVE_INSTRUCTION = (
    "Create the strongest truthful one-page ATS resume for this job. Prioritize "
    "directly relevant evidence, keep the writing natural and concise, retain "
    "enough supported evidence to create a visually complete page, and omit "
    "unsupported requirements rather than inventing them."
)


def native_resume_v5_enabled() -> bool:
    """Native V5 is available by default and may be explicitly disabled."""
    raw = os.getenv(_NATIVE_V5_ENV)
    if raw is None or not raw.strip():
        return True
    return raw.strip().casefold() in {"1", "true", "yes", "on", "enabled"}


def _request_engine_choice(job_id: int) -> None:
    st.session_state[_DIALOG_JOB_KEY] = int(job_id)


def _job_label(row: dict[str, Any]) -> str:
    score = (
        f" · {float(row['hunter_score']):.0f}% match"
        if row.get("hunter_score") is not None
        else ""
    )
    return (
        f"#{int(row['id'])} · {row.get('company_name') or 'Company'} · "
        f"{row.get('title') or 'Untitled role'}{score}"
    )


def _prime_native_job_selection(job_id: int) -> None:
    from app.native_resume_service_v5 import resume_job_options

    for row in resume_job_options(limit=300):
        if int(row["id"]) == int(job_id):
            st.session_state["native_resume_job_id"] = int(job_id)
            st.session_state["native_resume_job_choice"] = _job_label(row)
            return


def _set_view(view: str) -> None:
    st.session_state["product_view"] = view
    try:
        st.query_params["view"] = view
        for parameter in ("job", "pipeline_job", "tab", "section"):
            if parameter in st.query_params:
                del st.query_params[parameter]
    except Exception:  # noqa: BLE001
        return


def _route_to_native_studio(job_id: int) -> None:
    _prime_native_job_selection(job_id)
    _set_view("resume-studio")


def _route_to_profile() -> None:
    _set_view("profile")


def _job_summary(job_id: int) -> dict[str, Any]:
    from app.database import get_connection

    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id,company_name,title,location_raw,hunter_score FROM jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        connection.close()


def _run_n8n(job_id: int) -> dict[str, Any]:
    # Explicit n8n choice uses the existing guarded stored-job runner directly.
    from app.stored_job_n8n_worker import start_stored_job_run

    result = start_stored_job_run(int(job_id), actor="dashboard")
    if not isinstance(result, dict):
        raise TypeError(
            "The guarded n8n preparation path returned an invalid result."
        )
    if result.get("success") is not True:
        raise RuntimeError(
            str(
                result.get("message")
                or "The guarded n8n preparation path was blocked."
            )
        )
    return result


def _native_readiness() -> tuple[bool, str]:
    if not native_resume_v5_enabled():
        return False, "Native Resume is disabled for this environment."

    from app.native_resume_service_v5 import writer_status
    from app.phase45_truth_binding import current_candidate_profile_snapshot

    try:
        current_candidate_profile_snapshot()
    except Exception as error:  # noqa: BLE001
        return False, str(error)

    status = writer_status()
    if not status.get("configured"):
        return False, "Native Resume setup is incomplete."
    return True, "Ready to generate from your MUNSHI Profile."


def _run_native(job_id: int) -> tuple[bool, str]:
    """Generate and materialize Native V5 immediately from Candidate Truth."""
    if not native_resume_v5_enabled():
        return False, "Native Resume is temporarily unavailable."

    from app import native_resume_artifact_v5 as artifacts
    from app.native_resume_service_v5 import generate_resume, writer_status
    from app.phase45_truth_binding import current_candidate_profile_snapshot

    try:
        current_candidate_profile_snapshot()
    except Exception:  # noqa: BLE001
        _route_to_profile()
        return (
            False,
            "Complete your MUNSHI Profile before generating a Native resume.",
        )

    status = writer_status()
    if not status.get("configured"):
        st.session_state["native_resume_advanced_open"] = True
        _route_to_native_studio(job_id)
        return (
            False,
            "Open Advanced resume settings to finish Native Resume setup.",
        )

    record = generate_resume(
        job_id=int(job_id),
        instruction=_DEFAULT_NATIVE_INSTRUCTION,
        rewrite_mode="medium",
    )
    if record.get("stage_b_bound") is not True:
        raise RuntimeError("Native Resume V5 returned an unbound resume version.")

    bundle = artifacts.materialize(str(record["version_id"]))
    verification = bundle.get("page_verification") or {}
    if verification.get("status") != "PASS":
        raise RuntimeError("Native Resume page-fit verification did not pass.")

    st.session_state["native_resume_selected_version"] = record["version_id"]
    st.session_state[
        f"native_resume_page_verification_{record['version_id']}"
    ] = verification
    _route_to_native_studio(job_id)
    return (
        True,
        f"Native resume v{record['version_number']} created and page-fit checked.",
    )


@st.dialog("Choose resume engine", width="small")
def _engine_dialog(job_id: int) -> None:
    row = _job_summary(job_id)
    if not row:
        st.error("This stored job is no longer available.")
        st.session_state.pop(_DIALOG_JOB_KEY, None)
        return

    st.markdown(
        f"**{row.get('company_name') or 'Company'} · "
        f"{row.get('title') or 'Untitled role'}**"
    )
    st.caption(
        "Choose how MUNSHI should prepare the tailored resume for this job. "
        "You will review everything before any submission step."
    )

    choices = ["n8n"]
    if native_resume_v5_enabled():
        choices.append("native")

    choice = st.radio(
        "Resume generation engine",
        choices,
        index=0,
        format_func=lambda value: (
            "n8n workflow"
            if value == "n8n"
            else "MUNSHI Native Resume"
        ),
        key=f"resume_engine_choice_{job_id}",
    )

    if choice == "n8n":
        st.info("Uses the existing guarded n8n resume preparation workflow.")
    else:
        ready, message = _native_readiness()
        st.info(
            "Built into MUNSHI. Uses your permanent Profile and this job to "
            "create a truthful, checked one-page resume."
        )
        if ready:
            st.success(message)
        else:
            st.warning(message)

    action, cancel = st.columns((1.7, 1), gap="small")
    with action:
        if st.button(
            "Continue",
            type="primary",
            use_container_width=True,
            key=f"resume_engine_continue_{job_id}",
        ):
            st.session_state.pop(_DIALOG_JOB_KEY, None)
            if choice == "n8n":
                try:
                    result = _run_n8n(job_id)
                    if _PAGES is not None and hasattr(
                        _PAGES,
                        "_set_action_feedback",
                    ):
                        _PAGES._set_action_feedback(
                            "success" if result.get("started") else "info",
                            str(
                                result.get("message")
                                or "The guarded n8n preparation request was accepted."
                            ),
                        )
                except Exception as error:  # noqa: BLE001
                    if _PAGES is not None and hasattr(
                        _PAGES,
                        "_set_action_feedback",
                    ):
                        _PAGES._set_action_feedback(
                            "error",
                            "The guarded n8n preparation request could not be started: "
                            + str(error),
                        )
                st.rerun()

            try:
                with st.spinner(
                    "Creating your tailored Native resume and checking the final page…"
                ):
                    generated, message = _run_native(job_id)
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
                return

            if _PAGES is not None and hasattr(_PAGES, "_set_action_feedback"):
                _PAGES._set_action_feedback(
                    "success" if generated else "warning",
                    message,
                )
            st.rerun()

    with cancel:
        if st.button(
            "Cancel",
            use_container_width=True,
            key=f"resume_engine_cancel_{job_id}",
        ):
            st.session_state.pop(_DIALOG_JOB_KEY, None)
            st.rerun()


def render_resume_engine_selector() -> None:
    raw = st.session_state.get(_DIALOG_JOB_KEY)
    if raw is None:
        return
    try:
        job_id = int(raw)
    except (TypeError, ValueError):
        st.session_state.pop(_DIALOG_JOB_KEY, None)
        return
    _engine_dialog(job_id)


def install_resume_engine_selector(pages_module: Any) -> None:
    """Wrap the current Prepare action without deleting the proven n8n path."""
    global _PAGES
    _PAGES = pages_module
    if getattr(pages_module, "_resume_engine_selector_installed", False):
        return

    pages_module._resume_engine_original_n8n_prepare = pages_module._prepare_job
    pages_module._prepare_job = _request_engine_choice
    pages_module._resume_engine_selector_installed = True
