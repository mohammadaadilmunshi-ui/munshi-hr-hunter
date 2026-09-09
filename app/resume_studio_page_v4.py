"""Candidate-Truth-first Native Resume Studio.

The Profile section owns Master Resume intake, extraction, candidate edits, and
application defaults. Resume Studio consumes that one canonical Candidate Truth
snapshot and focuses only on job-specific resume creation, review, refinement,
and checked file downloads.
"""
from __future__ import annotations

import re

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


def _resume_job_catalog(limit: int = 500) -> list[dict[str, Any]]:
    from app.database import get_connection

    connection = get_connection()
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        job_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
        }
        wanted = [
            "id",
            "company_name",
            "title",
            "location_raw",
            "hunter_score",
            "employment_type",
            "remote_type",
            "source",
            "first_seen_at",
            "date_posted",
            "description_raw",
        ]
        selected = [name for name in wanted if name in job_columns]
        if "id" not in selected:
            return []

        rows = [
            dict(row)
            for row in connection.execute(
                f"SELECT {', '.join(selected)} FROM jobs "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        ]

        state_by_job: dict[int, dict[str, Any]] = {}
        if "product_job_state" in tables:
            for row in connection.execute(
                "SELECT job_id,saved,skipped FROM product_job_state"
            ).fetchall():
                state_by_job[int(row["job_id"])] = dict(row)

        prepared: set[int] = set()
        if "n8n_results" in tables:
            result_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(n8n_results)"
                ).fetchall()
            }
            artifact_columns = [
                name
                for name in (
                    "resume_pdf_url",
                    "resume_doc_url",
                    "resume_docx_url",
                    "cover_letter_doc_url",
                )
                if name in result_columns
            ]
            if artifact_columns:
                predicate = " OR ".join(
                    f"trim(COALESCE({name},'')) != ''"
                    for name in artifact_columns
                )
                for row in connection.execute(
                    f"SELECT DISTINCT job_id FROM n8n_results WHERE {predicate}"
                ).fetchall():
                    prepared.add(int(row["job_id"]))

        if "native_resume_versions" in tables:
            for row in connection.execute(
                "SELECT DISTINCT job_id FROM native_resume_versions"
            ).fetchall():
                prepared.add(int(row["job_id"]))

        for row in rows:
            job_id = int(row["id"])
            state = state_by_job.get(job_id, {})
            row["saved"] = bool(state.get("saved"))
            row["skipped"] = bool(state.get("skipped"))
            row["prepared"] = job_id in prepared
        return rows
    finally:
        connection.close()


def _job_search_blob(row: dict[str, Any]) -> str:
    values = (
        row.get("id"),
        row.get("company_name"),
        row.get("title"),
        row.get("location_raw"),
        row.get("source"),
        row.get("employment_type"),
        row.get("remote_type"),
        row.get("description_raw"),
    )
    return " ".join(str(value or "") for value in values).casefold()


def _filter_resume_jobs(
    rows: list[dict[str, Any]],
    *,
    query: str = "",
    result_set: str = "all",
    minimum_match: int = 0,
    sort_by: str = "match_desc",
) -> list[dict[str, Any]]:
    terms = [term for term in str(query or "").casefold().split() if term]
    result: list[dict[str, Any]] = []
    for row in rows:
        if result_set == "saved" and not row.get("saved"):
            continue
        if result_set == "prepared" and not row.get("prepared"):
            continue
        score = row.get("hunter_score")
        numeric_score = float(score) if score is not None else -1.0
        if numeric_score < int(minimum_match):
            continue
        blob = _job_search_blob(row)
        if terms and not all(term in blob for term in terms):
            continue
        result.append(row)

    if sort_by == "newest":
        result.sort(
            key=lambda row: (
                str(row.get("first_seen_at") or row.get("date_posted") or ""),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
    elif sort_by == "company":
        result.sort(
            key=lambda row: (
                str(row.get("company_name") or "").casefold(),
                str(row.get("title") or "").casefold(),
                -float(row.get("hunter_score") or -1),
            )
        )
    else:
        result.sort(
            key=lambda row: (
                float(row.get("hunter_score") or -1),
                int(row.get("id") or 0),
            ),
            reverse=True,
        )
    return result


def _set_resume_job(row: dict[str, Any]) -> None:
    job_id = int(row["id"])
    st.session_state["native_resume_job_id"] = job_id
    st.session_state["native_resume_job_choice"] = _job_label(row)


def _job_workspace() -> tuple[int | None, dict[str, Any]]:
    st.markdown("### Target job")
    rows = _resume_job_catalog(limit=500)
    if not rows:
        st.info("Save a job first, then return here to create its tailored resume.")
        return None, {}

    by_id = {int(row["id"]): row for row in rows}
    selected_id_raw = st.session_state.get("native_resume_job_id")
    try:
        selected_id = int(selected_id_raw)
    except (TypeError, ValueError):
        selected_id = 0

    if selected_id not in by_id:
        legacy = str(st.session_state.get("native_resume_job_choice") or "")
        match = re.match(r"#(\d+)\s*·", legacy)
        if match and int(match.group(1)) in by_id:
            selected_id = int(match.group(1))

    if selected_id not in by_id:
        ranked = _filter_resume_jobs(rows)
        selected_id = int(ranked[0]["id"])
        _set_resume_job(by_id[selected_id])

    context = resume_v5.job_context(selected_id)

    with st.container(border=True):
        st.caption("Selected target")
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

    with st.expander("Find or change job", expanded=True):
        search_columns = st.columns((3.2, 1.25, 1.1, 1.25), gap="small")
        with search_columns[0]:
            query = st.text_input(
                "Find a job",
                placeholder=(
                    "Search company, title, location, source, JD keyword, or job #…"
                ),
                key="resume_studio_job_search",
                label_visibility="collapsed",
            )
        with search_columns[1]:
            result_set = st.selectbox(
                "Jobs",
                ["all", "saved", "prepared"],
                format_func=lambda value: {
                    "all": "All jobs",
                    "saved": "Saved",
                    "prepared": "Prepared",
                }[value],
                key="resume_studio_job_result_set",
                label_visibility="collapsed",
            )
        with search_columns[2]:
            minimum_match = st.selectbox(
                "Match",
                [0, 50, 70, 85],
                format_func=lambda value: (
                    "Any match" if value == 0 else f"{value}%+"
                ),
                key="resume_studio_job_minimum_match",
                label_visibility="collapsed",
            )
        with search_columns[3]:
            sort_by = st.selectbox(
                "Sort",
                ["match_desc", "newest", "company"],
                format_func=lambda value: {
                    "match_desc": "Best match",
                    "newest": "Newest",
                    "company": "Company A–Z",
                }[value],
                key="resume_studio_job_sort",
                label_visibility="collapsed",
            )

        filtered = _filter_resume_jobs(
            rows,
            query=query,
            result_set=result_set,
            minimum_match=int(minimum_match),
            sort_by=sort_by,
        )
        st.caption(
            f"{len(filtered)} matching job{'s' if len(filtered) != 1 else ''}. "
            "Choose one to tailor against its exact stored job snapshot."
        )

        visible_limit = st.selectbox(
            "Results shown",
            [8, 16, 32],
            format_func=lambda value: f"Show {value}",
            key="resume_studio_job_visible_limit",
        )

        if not filtered:
            st.info("No jobs match those filters.")
        else:
            for row in filtered[: int(visible_limit)]:
                job_id = int(row["id"])
                with st.container(border=True):
                    left, right = st.columns((5, 1.2), gap="small")
                    with left:
                        st.markdown(
                            f"**{esc(row.get('company_name'), 'Company')} · "
                            f"{esc(row.get('title'), 'Untitled role')}**",
                            unsafe_allow_html=True,
                        )
                        meta = [
                            row.get("location_raw"),
                            row.get("source"),
                            "Saved" if row.get("saved") else None,
                            "Prepared" if row.get("prepared") else None,
                        ]
                        st.caption(
                            " · ".join(str(value) for value in meta if value)
                        )
                    with right:
                        score = row.get("hunter_score")
                        st.markdown(
                            f"**{float(score):.0f}% match**"
                            if score is not None
                            else "**Match n/a**"
                        )
                        if job_id == selected_id:
                            st.button(
                                "Selected",
                                disabled=True,
                                use_container_width=True,
                                key=f"resume_job_selected_{job_id}",
                            )
                        elif st.button(
                            "Use job",
                            use_container_width=True,
                            key=f"resume_job_use_{job_id}",
                        ):
                            _set_resume_job(row)
                            st.rerun()

            if len(filtered) > int(visible_limit):
                st.caption(
                    f"{len(filtered) - int(visible_limit)} more results. "
                    "Refine the search or increase Results shown."
                )

    with st.expander("Review job description", expanded=False):
        st.write(context.get("description_raw") or "No stored description.")
        if context.get("qualifications"):
            st.markdown("**Qualifications**")
            st.write(context["qualifications"])
    return selected_id, context


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
