"""Explicit user choice between the proven n8n writer and native Resume Studio.

This module is deliberately a routing layer, not submission authority.  It keeps
n8n available as the default/proven preparation path while allowing a candidate
to explicitly choose the built-in evidence-backed native writer for a stored
job.  Neither choice marks an application Submitted.
"""
from __future__ import annotations

from typing import Any, Callable

import streamlit as st


_PAGES: Any = None
_DIALOG_JOB_KEY = "resume_engine_choice_job_id"
_DEFAULT_NATIVE_INSTRUCTION = (
    "Create the strongest truthful one-page ATS resume for this job. Prioritize "
    "directly relevant evidence, keep the writing natural and concise, and omit "
    "unsupported requirements rather than inventing them."
)


def _request_engine_choice(job_id: int) -> None:
    """Open the engine chooser on the next Streamlit render."""
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
    """Make Resume Studio open on the exact job the candidate selected."""
    from app.native_resume_service import resume_job_options

    for row in resume_job_options(limit=300):
        if int(row["id"]) == int(job_id):
            st.session_state["native_resume_job_choice"] = _job_label(row)
            return


def _route_to_native_studio(job_id: int) -> None:
    _prime_native_job_selection(job_id)
    st.session_state["product_view"] = "resume-studio"
    try:
        st.query_params["view"] = "resume-studio"
        for parameter in ("job", "pipeline_job", "tab", "section"):
            if parameter in st.query_params:
                del st.query_params[parameter]
    except Exception:
        pass


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


def _run_n8n(job_id: int) -> None:
    runner: Callable[[int], None] | None = getattr(
        _PAGES, "_resume_engine_original_n8n_prepare", None
    )
    if not callable(runner):
        raise RuntimeError("The guarded n8n preparation path is unavailable.")
    runner(int(job_id))


def _run_native(job_id: int) -> tuple[bool, str]:
    """Run native generation only when its explicit prerequisites are present."""
    from app.native_resume_service import active_source, generate_resume, model_status

    source = active_source()
    status = model_status()
    if not source:
        _route_to_native_studio(job_id)
        return False, "Native Resume Studio needs a confirmed resume source first."
    if not status.get("configured"):
        _route_to_native_studio(job_id)
        return False, "Native Resume Studio is installed, but its OpenAI runtime key is not configured on this server."

    record = generate_resume(
        job_id=int(job_id),
        instruction=_DEFAULT_NATIVE_INSTRUCTION,
    )
    st.session_state["native_resume_selected_version"] = record["version_id"]
    _route_to_native_studio(job_id)
    return True, f"Native ATS resume v{record['version_number']} validated."


@st.dialog("Choose resume engine", width="small")
def _engine_dialog(job_id: int) -> None:
    row = _job_summary(job_id)
    if not row:
        st.error("This stored job is no longer available.")
        st.session_state.pop(_DIALOG_JOB_KEY, None)
        return

    st.markdown(
        f"**{row.get('company_name') or 'Company'} · {row.get('title') or 'Untitled role'}**"
    )
    st.caption(
        "Choose how MUNSHI should generate the resume for this preparation. "
        "n8n remains available and is the default proven path."
    )

    choice = st.radio(
        "Resume generation engine",
        ["n8n", "native"],
        index=0,
        format_func=lambda value: (
            "n8n workflow · proven current pipeline"
            if value == "n8n"
            else "Native Resume Studio · built into MUNSHI"
        ),
        key=f"resume_engine_choice_{job_id}",
    )

    if choice == "n8n":
        st.info(
            "Runs the existing guarded n8n preparation workflow. This keeps the "
            "current resume/package pipeline intact and does not claim submission."
        )
    else:
        try:
            from app.native_resume_service import active_source, model_status

            status = model_status()
            source_ready = bool(active_source())
            writer_ready = bool(status.get("configured"))
        except Exception:
            source_ready = False
            writer_ready = False
            status = {"model": "Unavailable"}

        st.info(
            "Runs MUNSHI's evidence-backed native writer for this exact stored job. "
            "The generated version is immutable, reviewable, and does not mark the "
            "application Submitted."
        )
        readiness = (
            "Ready to generate now"
            if source_ready and writer_ready
            else "Opens Resume Studio to finish native-writer setup"
        )
        st.caption(
            f"Native status: {readiness} · model {status.get('model') or 'not configured'}"
        )

    action, cancel = st.columns((1.7, 1), gap="small")
    with action:
        if st.button(
            "Continue with selected engine",
            type="primary",
            use_container_width=True,
            key=f"resume_engine_continue_{job_id}",
        ):
            st.session_state.pop(_DIALOG_JOB_KEY, None)
            if choice == "n8n":
                try:
                    _run_n8n(job_id)
                except Exception:
                    if _PAGES is not None and hasattr(_PAGES, "_set_action_feedback"):
                        _PAGES._set_action_feedback(
                            "error",
                            "The guarded n8n preparation request could not be started. No submission was claimed.",
                        )
                st.rerun()

            try:
                with st.spinner(
                    "Building evidence, generating the native resume, and running truth/ATS guards…"
                ):
                    generated, message = _run_native(job_id)
            except Exception as error:
                st.error(str(error))
                return
            if _PAGES is not None and hasattr(_PAGES, "_set_action_feedback"):
                _PAGES._set_action_feedback("success" if generated else "warning", message)
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
    """Render at most one chooser after the current product page."""
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
    """Wrap Product UI preparation without deleting the existing n8n path."""
    global _PAGES
    _PAGES = pages_module
    if getattr(pages_module, "_resume_engine_selector_installed", False):
        return

    pages_module._resume_engine_original_n8n_prepare = pages_module._prepare_job
    pages_module._prepare_job = _request_engine_choice
    pages_module._resume_engine_selector_installed = True
