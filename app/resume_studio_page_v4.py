"""Candidate-Truth-first Native Resume Studio.

The Profile section owns Master Resume intake, extraction, candidate edits, and
application defaults. Resume Studio consumes that one canonical Candidate Truth
snapshot and focuses only on job-specific resume creation, review, refinement,
and checked file downloads.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5
from app import phase45_truth_binding as truth_binding
from app import resume_studio_page_v2 as advanced_writer_ui
from app.product_ui import esc, page_intro

STYLE_LABELS = {
    "slight": "Light",
    "medium": "Balanced",
    "aggressive": "Strong",
}
STYLE_HELP = {
    "slight": "Keeps the current structure and makes targeted JD-specific improvements.",
    "medium": "Balances ATS alignment, readability, and preservation of your career story.",
    "aggressive": "Rebuilds emphasis around the JD while remaining strictly evidence-bound.",
}


def _set_view(view: str) -> None:
    st.session_state["product_view"] = view
    try:
        st.query_params["view"] = view
        for parameter in ("job", "pipeline_job", "tab", "section"):
            if parameter in st.query_params:
                del st.query_params[parameter]
    except Exception:  # noqa: BLE001
        return


def _profile_snapshot() -> tuple[dict[str, Any] | None, str | None]:
    try:
        return truth_binding.current_candidate_profile_snapshot(), None
    except Exception as error:  # noqa: BLE001
        return None, str(error)


def _profile_card(snapshot: dict[str, Any] | None, error: str | None) -> None:
    with st.container(border=True):
        left, right = st.columns((4, 1.3), gap="medium")
        with left:
            st.markdown("### Your MUNSHI Profile")
            if snapshot:
                facts = [
                    fact
                    for fact in snapshot.get("facts") or []
                    if fact.get("protected") is not True
                ]
                st.success(
                    f"Profile ready · {len(facts)} resume-relevant verified facts available."
                )
                st.caption(
                    "Resume Studio reads this permanent Profile directly. "
                    "Master Resume extraction and profile editing stay in Profile."
                )
            else:
                st.warning(
                    error
                    or "Complete your MUNSHI Profile before creating a Native resume."
                )
        with right:
            if st.button(
                "Open Profile",
                use_container_width=True,
                key="resume_studio_v4_open_profile",
            ):
                _set_view("profile")
                st.rerun()


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


def _job_workspace() -> tuple[int | None, dict[str, Any]]:
    st.markdown("### Target job")
    rows = resume_v5.resume_job_options(limit=300)
    if not rows:
        st.info("Save a job first, then return here to create its tailored resume.")
        return None, {}

    labels = {_job_label(row): row for row in rows}
    options = list(labels)
    current = st.session_state.get("native_resume_job_choice")
    if current not in labels:
        st.session_state["native_resume_job_choice"] = options[0]

    selected_label = st.selectbox(
        "Job",
        options,
        key="native_resume_job_choice",
        label_visibility="collapsed",
    )
    selected = labels[selected_label]
    job_id = int(selected["id"])
    context = resume_v5.job_context(job_id)

    with st.container(border=True):
        st.markdown(
            f"**{esc(context.get('company_name'), 'Company')} · "
            f"{esc(context.get('title'), 'Untitled role')}**",
            unsafe_allow_html=True,
        )
        details = [
            context.get("location_raw"),
            context.get("employment_type"),
            context.get("remote_type"),
        ]
        st.caption(" · ".join(str(value) for value in details if value))
        if context.get("hunter_score") is not None:
            st.caption(f"Hunter match: {float(context['hunter_score']):.0f}%")

    with st.expander("Review job description", expanded=False):
        st.write(context.get("description_raw") or "No stored description.")
        if context.get("qualifications"):
            st.markdown("**Qualifications**")
            st.write(context["qualifications"])
    return job_id, context


def _style_selector(key: str, default: str = "medium") -> str:
    options = ["slight", "medium", "aggressive"]
    mode = st.radio(
        "Tailoring strength",
        options,
        index=options.index(default),
        horizontal=True,
        format_func=lambda value: STYLE_LABELS[value],
        key=key,
    )
    st.caption(STYLE_HELP[mode])
    return mode


def _generate_checked(
    *,
    job_id: int,
    rewrite_mode: str,
    instruction: str,
    parent_version_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    record = resume_v5.generate_resume(
        job_id=job_id,
        instruction=instruction,
        rewrite_mode=rewrite_mode,
        parent_version_id=parent_version_id,
    )
    if record.get("stage_b_bound") is not True:
        raise RuntimeError("Native Resume did not return a Stage B-bound version.")
    bundle = artifacts.materialize(str(record["version_id"]))
    verification = bundle.get("page_verification") or {}
    if verification.get("status") != "PASS":
        raise RuntimeError("The generated resume did not pass page-fit verification.")
    return record, bundle


def _generation_panel(
    *,
    job_id: int,
    profile_ready: bool,
    writer_ready: bool,
) -> None:
    st.markdown("### Create tailored resume")
    mode = _style_selector(f"resume_studio_v4_mode_{job_id}")

    with st.expander("Optional tailoring note", expanded=False):
        instruction = st.text_area(
            "Direction",
            placeholder=(
                "Example: emphasize people analytics and Power BI while keeping "
                "recruiting operations visible."
            ),
            height=90,
            key=f"resume_studio_v4_instruction_{job_id}",
            label_visibility="collapsed",
        )

    disabled = not (profile_ready and writer_ready)
    if not profile_ready:
        st.warning("Complete your Profile before generating.")
    elif not writer_ready:
        st.warning(
            "Native Resume setup is incomplete. Open Advanced resume settings below."
        )

    if st.button(
        "Generate resume",
        type="primary",
        use_container_width=True,
        key=f"resume_studio_v4_generate_{job_id}",
        disabled=disabled,
    ):
        try:
            with st.spinner(
                "Tailoring from your Profile, checking truth and JD coverage, "
                "and building a full-page PDF…"
            ):
                record, bundle = _generate_checked(
                    job_id=job_id,
                    rewrite_mode=mode,
                    instruction=instruction.strip(),
                )
        except Exception as error:  # noqa: BLE001
            st.error(str(error))
        else:
            version_id = str(record["version_id"])
            st.session_state["native_resume_selected_version"] = version_id
            st.session_state[
                f"native_resume_page_verification_{version_id}"
            ] = bundle["page_verification"]
            st.success(
                f"Resume v{record['version_number']} created and page-fit checked."
            )
            st.rerun()


def _artifact_bundle(version_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = artifacts.list_artifacts(version_id)
    by_kind = {row["artifact_kind"]: row for row in rows}
    if set(by_kind) != {"DOCX", "PDF"}:
        return {}, {}
    try:
        quality = artifacts.pdf_page_quality(version_id)
    except Exception as error:  # noqa: BLE001
        quality = {"status": "FAIL", "error": str(error)}
    return by_kind, quality


def _downloads(version_id: str, by_kind: dict[str, Any]) -> None:
    if set(by_kind) != {"DOCX", "PDF"}:
        st.warning("Checked DOCX/PDF files have not been built for this version.")
        if st.button(
            "Build checked files",
            use_container_width=True,
            key=f"resume_studio_v4_build_artifacts_{version_id}",
        ):
            try:
                with st.spinner("Building and checking the final DOCX/PDF…"):
                    bundle = artifacts.materialize(version_id)
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                st.session_state[
                    f"native_resume_page_verification_{version_id}"
                ] = bundle["page_verification"]
                st.rerun()
        return

    left, right = st.columns(2, gap="small")
    with left:
        docx = by_kind["DOCX"]
        st.download_button(
            "Download DOCX",
            data=artifacts.artifact_bytes(str(docx["artifact_id"])),
            file_name=str(docx["filename"]),
            mime=str(docx["mime_type"]),
            use_container_width=True,
            key=f"resume_studio_v4_docx_{version_id}",
        )
    with right:
        pdf = by_kind["PDF"]
        st.download_button(
            "Download PDF",
            data=artifacts.artifact_bytes(str(pdf["artifact_id"])),
            file_name=str(pdf["filename"]),
            mime=str(pdf["mime_type"]),
            use_container_width=True,
            key=f"resume_studio_v4_pdf_{version_id}",
        )


def _version_workspace(job_id: int, context: dict[str, Any], writer_ready: bool) -> None:
    versions = [
        row
        for row in resume_v5.list_versions(job_id=job_id, limit=100)
        if row.get("stage_b_bound") is True
    ]
    if not versions:
        return

    st.divider()
    st.markdown("### Resume preview")

    ids = [str(row["version_id"]) for row in versions]
    selected = st.session_state.get("native_resume_selected_version")
    if selected not in ids:
        st.session_state["native_resume_selected_version"] = ids[0]

    by_id = {str(row["version_id"]): row for row in versions}
    selected_id = st.selectbox(
        "Version",
        ids,
        format_func=lambda value: (
            f"v{by_id[value]['version_number']} · "
            f"{STYLE_LABELS.get((by_id[value].get('diagnostics') or {}).get('rewrite_mode'), 'Tailored')}"
        ),
        key="native_resume_selected_version",
    )
    record = resume_v5.get_version(selected_id)
    diagnostics = record.get("diagnostics") or {}
    artifact_rows, quality = _artifact_bundle(selected_id)

    metrics = st.columns(4, gap="small")
    metrics[0].metric("Version", f"v{record['version_number']}")
    metrics[1].metric(
        "Truth check",
        str((diagnostics.get("truth_audit") or {}).get("status") or "PASS"),
    )
    metrics[2].metric(
        "JD coverage",
        f"{float(diagnostics.get('jd_term_coverage_percent') or 0):.0f}%",
    )
    if quality.get("status") == "PASS":
        metrics[3].metric(
            "Page fit",
            f"{float(quality.get('occupied_height_ratio') or 0) * 100:.0f}%",
        )
    else:
        metrics[3].metric("Page fit", "Needs check")

    components.html(resume_v5.version_html(selected_id), height=1075, scrolling=True)

    if quality.get("status") == "PASS":
        st.success("1 page · page fit verified · truth and evidence guards passed.")
    elif quality:
        st.warning(str(quality.get("error") or "Page fit needs to be rebuilt."))

    _downloads(selected_id, artifact_rows)

    with st.expander("Refine this resume", expanded=False):
        prior = diagnostics.get("rewrite_mode")
        default = prior if prior in STYLE_LABELS else "medium"
        mode = _style_selector(
            f"resume_studio_v4_revision_mode_{selected_id}",
            default=default,
        )
        instruction = st.text_area(
            "What should MUNSHI improve?",
            placeholder=(
                "Example: shorten the summary, move Power BI earlier, "
                "and reduce recruiting emphasis."
            ),
            height=90,
            key=f"resume_studio_v4_revision_instruction_{selected_id}",
        )
        if st.button(
            "Create revised version",
            type="primary",
            use_container_width=True,
            key=f"resume_studio_v4_revision_{selected_id}",
            disabled=not (writer_ready and instruction.strip()),
        ):
            try:
                with st.spinner(
                    "Creating a new immutable version and re-running all checks…"
                ):
                    revised, bundle = _generate_checked(
                        job_id=job_id,
                        rewrite_mode=mode,
                        instruction=instruction.strip(),
                        parent_version_id=selected_id,
                    )
            except Exception as error:  # noqa: BLE001
                st.error(str(error))
            else:
                version_id = str(revised["version_id"])
                st.session_state["native_resume_selected_version"] = version_id
                st.session_state[
                    f"native_resume_page_verification_{version_id}"
                ] = bundle["page_verification"]
                st.rerun()

    with st.expander("Advanced diagnostics", expanded=False):
        st.write(
            {
                "ats_readiness": diagnostics.get("ats_readiness_estimate"),
                "matched_jd_terms": diagnostics.get("matched_jd_terms") or [],
                "missing_jd_terms": diagnostics.get("missing_jd_terms") or [],
                "writer_model": diagnostics.get("writer_model"),
                "writer_reasoning": diagnostics.get("writer_reasoning_effort"),
                "writer_calls": diagnostics.get("writer_api_calls"),
            }
        )


def render() -> None:
    resume_v5.ensure_schema()
    artifacts.ensure_schema()

    page_intro(
        "RESUME STUDIO",
        "Resume Studio",
        "Create polished, truthful job-specific resumes directly from your MUNSHI Profile.",
    )

    snapshot, profile_error = _profile_snapshot()
    _profile_card(snapshot, profile_error)

    writer_status = resume_v5.writer_status()
    writer_ready = bool(writer_status.get("configured"))

    with st.expander(
        "Advanced resume settings",
        expanded=bool(st.session_state.get("native_resume_advanced_open")),
    ):
        advanced_writer_ui._writer_settings_panel()

    st.divider()
    job_id, context = _job_workspace()
    if job_id is None:
        return

    st.divider()
    _generation_panel(
        job_id=job_id,
        profile_ready=snapshot is not None,
        writer_ready=writer_ready,
    )
    _version_workspace(job_id, context, writer_ready)
