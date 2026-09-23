"""Candidate-Truth-first Native Resume Studio.

The Profile section owns Master Resume intake, extraction, candidate edits, and
application defaults. Resume Studio consumes that one canonical Candidate Truth
snapshot and focuses only on job-specific resume creation, review, refinement,
and checked file downloads.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app import native_resume_artifact_v5 as artifacts
from app import native_resume_service_v5 as resume_v5
from app import phase45_truth_binding as truth_binding
from app import resume_studio_page_v2 as advanced_writer_ui
from app.product_ui import esc, page_intro, pastel_for, score_ring

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


def _open_job_view(
    job_id: int,
    *,
    application: bool = False,
    application_id: str | None = None,
) -> None:
    view = "prepare-application" if application else "jobs"
    st.session_state["product_view"] = view
    st.session_state["last_viewed_job_id"] = int(job_id)
    try:
        st.query_params["view"] = view
        st.query_params["job"] = str(int(job_id))
        if application and application_id:
            st.query_params["application"] = str(application_id)
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
            "salary_raw",
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


def _relative_job_time(value: Any) -> str:
    if not value:
        return "Date not recorded"
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        delta = datetime.now(date.tzinfo) - date
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return "Seen recently"
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except (TypeError, ValueError):
        return "Date recorded"


def _resume_job_card_html(
    row: dict[str, Any],
    *,
    selected: bool = False,
) -> str:
    job_id = int(row["id"])
    tone = pastel_for(job_id)
    tags = [
        row.get("remote_type"),
        row.get("employment_type"),
        row.get("salary_raw"),
        "Saved" if row.get("saved") else None,
        "Prepared" if row.get("prepared") else None,
        "Selected" if selected else None,
    ]
    tags_html = "".join(
        f'<span class="tag">{esc(tag)}</span>'
        for tag in tags
        if str(tag or "").strip()
    )
    return f"""<div class="job-card resume-studio-job-card">
        <div class="job-card-main" style="--card-bg:{tone}">
            <div class="job-top">
                <span>{esc(row.get("location_raw"), "Location unknown")}<br>
                <span class="quiet">{esc(_relative_job_time(row.get("first_seen_at")))}</span></span>
                {score_ring(row.get("hunter_score"), tone)}
            </div>
            <div class="job-title">{esc(row.get("title"), "Untitled role")}</div>
            <div>{tags_html}</div>
            <div class="job-company">{esc(row.get("company_name"), "Company not recorded")}</div>
            <div class="job-meta">{esc(row.get("source"), "Source unknown")}</div>
        </div>
    </div>"""


def _set_resume_job(row: dict[str, Any]) -> None:
    job_id = int(row["id"])
    st.session_state["native_resume_job_id"] = job_id
    st.session_state["native_resume_job_choice"] = _job_label(row)
    recent = [
        int(value)
        for value in st.session_state.get("resume_studio_recent_job_ids", [])
        if str(value).isdigit() and int(value) != job_id
    ]
    st.session_state["resume_studio_recent_job_ids"] = [job_id, *recent][:8]


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
        candidates = [st.session_state.get("last_viewed_job_id")]
        candidates.extend(st.session_state.get("resume_studio_recent_job_ids", []))
        for candidate in candidates:
            try:
                candidate_id = int(candidate)
            except (TypeError, ValueError):
                continue
            if candidate_id in by_id:
                selected_id = candidate_id
                break

    if selected_id not in by_id:
        ranked = _filter_resume_jobs(rows)
        selected_id = int(ranked[0]["id"])

    _set_resume_job(by_id[selected_id])
    selected_row = by_id[selected_id]
    context = resume_v5.job_context(selected_id)

    st.markdown(
        """<style>
        .resume-studio-job-card .job-card-main{
            border-radius:15px!important;
            min-height:224px!important;
        }
        [class*="st-key-resume_job_result_"]{
            border:1px solid var(--m-border);
            border-radius:18px;
            overflow:hidden;
            background:#FFF;
            padding:0 0 .7rem!important;
            box-shadow:0 5px 16px rgba(20,33,41,.045);
            height:100%;
        }
        [class*="st-key-resume_job_result_"] .stButton>button{
            width:calc(100% - 1.2rem)!important;
            margin:.65rem .6rem 0!important;
        }
        </style>""",
        unsafe_allow_html=True,
    )

    st.caption("Selected target")
    selected_card, selected_copy = st.columns((1.05, 1.75), gap="large")
    with selected_card:
        st.markdown(
            _resume_job_card_html(selected_row, selected=True),
            unsafe_allow_html=True,
        )
    with selected_copy:
        st.markdown("### Ready to tailor this job")
        st.write(
            "MUNSHI keeps the job context with you automatically. "
            "You never need to remember or enter a job ID."
        )
        details = [
            context.get("location_raw"),
            context.get("employment_type"),
            context.get("remote_type"),
        ]
        if any(details):
            st.caption(" · ".join(str(value) for value in details if value))
        if context.get("hunter_score") is not None:
            st.caption(f"Hunter match: {float(context['hunter_score']):.0f}%")

    with st.expander("Change target job", expanded=False):
        search_columns = st.columns((3.2, 1.25, 1.1, 1.25), gap="small")
        with search_columns[0]:
            query = st.text_input(
                "Find a job",
                placeholder=(
                    "Search company, title, location, source, or JD keyword…"
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

        visible_rows = filtered[:6]
        if not visible_rows:
            st.info("No jobs match those filters.")
        else:
            grid = st.columns(3, gap="medium")
            for index, row in enumerate(visible_rows):
                job_id = int(row["id"])
                with grid[index % 3]:
                    with st.container(key=f"resume_job_result_{job_id}"):
                        st.markdown(
                            _resume_job_card_html(
                                row,
                                selected=job_id == selected_id,
                            ),
                            unsafe_allow_html=True,
                        )
                        if job_id == selected_id:
                            st.button(
                                "Selected",
                                disabled=True,
                                use_container_width=True,
                                key=f"resume_job_selected_{job_id}",
                            )
                        elif st.button(
                            "Use this job",
                            use_container_width=True,
                            key=f"resume_job_use_{job_id}",
                        ):
                            _set_resume_job(row)
                            st.rerun()

            if len(filtered) > len(visible_rows):
                st.caption(
                    f"{len(filtered) - len(visible_rows)} more matching jobs. "
                    "Refine the search to narrow the card grid."
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


def _reusable_exact_resume(job_id: int) -> dict[str, Any] | None:
    """Return a valid exact-job/current-truth version without a model call."""
    from app.phase67_common import safe_owned_job_snapshot

    try:
        truth = truth_binding.current_candidate_profile_snapshot()
        job = safe_owned_job_snapshot(int(job_id))
    except Exception:  # noqa: BLE001
        return None
    for summary in resume_v5.list_versions(job_id=int(job_id), limit=12):
        if summary.get("stage_b_bound") is not True or _is_internal_one_page_repair(summary):
            continue
        try:
            record = resume_v5.get_version(str(summary["version_id"]))
        except Exception:  # noqa: BLE001
            continue
        candidate_binding = dict(record.get("candidate_truth_binding") or {})
        job_binding = dict(record.get("job_snapshot_binding") or {})
        if str(candidate_binding.get("profile_digest") or "") != str(
            truth.get("profile_digest") or ""
        ):
            continue
        if str(job_binding.get("job_snapshot_sha256") or "") != str(
            job.get("job_snapshot_sha256") or ""
        ):
            continue
        return record
    return None


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
    existing = _reusable_exact_resume(job_id)
    regenerate_key = f"resume_regenerate_requested_{job_id}"
    regenerate_requested = bool(st.session_state.get(regenerate_key))
    if existing and not regenerate_requested:
        current = existing
        st.success(
            f"A validated tailored resume already exists for this exact job "
            f"(v{int(current.get('version_number') or 0)}). MUNSHI will reuse it."
        )
        open_resume, regenerate = st.columns((2.2, 1), gap="small")
        if open_resume.button(
            "Open existing resume",
            key=f"resume_studio_reuse_{job_id}",
            type="primary",
            use_container_width=True,
        ):
            version_id = str(current["version_id"])
            try:
                bundle = artifacts.materialize(version_id)
            except Exception as error:  # noqa: BLE001
                st.error(f"The saved resume needs attention: {error}")
            else:
                st.session_state["native_resume_selected_version"] = version_id
                st.session_state[
                    f"native_resume_page_verification_{version_id}"
                ] = bundle["page_verification"]
                st.rerun()
        if regenerate.button(
            "Regenerate",
            key=f"resume_studio_request_regenerate_{job_id}",
            use_container_width=True,
        ):
            st.session_state[regenerate_key] = True
            st.rerun()
        st.caption(
            "Regenerate is optional and creates a new immutable version; the current version is preserved."
        )
        return

    if regenerate_requested:
        st.info("Regenerating will create a new version and preserve the current resume.")
    st.caption(
        "Analyzing job → Tailoring resume → Truth-checking → Fitting to one page → Ready"
    )
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
        "Regenerate resume" if regenerate_requested else "Generate resume",
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
            st.session_state.pop(regenerate_key, None)
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


_ONE_PAGE_REPAIR_PREFIX = (
    "Create a new compact revision of this exact resume. The current version "
    "failed the strict one-page guard"
)


def _is_internal_one_page_repair(row: dict[str, Any]) -> bool:
    return str(row.get("instruction") or "").startswith(_ONE_PAGE_REPAIR_PREFIX)


def _user_facing_versions(
    versions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Hide superseded internal repair passes while preserving immutable history."""
    repair_parent_ids = {
        str(row.get("parent_version_id") or "")
        for row in versions
        if _is_internal_one_page_repair(row) and row.get("parent_version_id")
    }
    visible = [
        row
        for row in versions
        if str(row.get("version_id") or "") not in repair_parent_ids
    ]
    return visible, max(0, len(versions) - len(visible))


def _resume_library_rows(limit: int = 200) -> list[dict[str, Any]]:
    """Build a customer-safe cross-job resume library with isolated status reads."""
    from app import product_application_flow_v2 as application_flow

    versions = [
        row
        for row in resume_v5.list_versions(limit=limit)
        if row.get("stage_b_bound") is True and not _is_internal_one_page_repair(row)
    ]
    jobs = {int(row["id"]): row for row in _resume_job_catalog(limit=500)}
    current_by_job: dict[int, str] = {}
    application_state: dict[int, tuple[str, str]] = {}
    result: list[dict[str, Any]] = []
    for version in versions:
        job_id = int(version["job_id"])
        job = jobs.get(job_id)
        if job is None:
            try:
                job = dict(resume_v5.job_context(job_id))
            except Exception:  # noqa: BLE001 - one stale job must not break library
                job = {"id": job_id, "company_name": "Company", "title": "Stored job"}
        current_by_job.setdefault(job_id, str(version["version_id"]))
        if job_id not in application_state:
            try:
                flow = application_flow.flow_for_job(job_id, summary=True)
                prepare = dict(flow.get("prepare") or {})
                lifecycle = dict(flow.get("lifecycle") or {})
                application_state[job_id] = (
                    str(flow.get("display_status") or "No application"),
                    str(
                        lifecycle.get("application_id")
                        or prepare.get("application_id")
                        or ""
                    ),
                )
            except Exception:  # noqa: BLE001 - preserve access to healthy resumes
                application_state[job_id] = ("Application status unavailable", "")
        status, application_id = application_state[job_id]
        diagnostics = dict(version.get("diagnostics") or {})
        result.append(
            {
                **version,
                "company_name": job.get("company_name") or "Company",
                "title": job.get("title") or "Stored job",
                "hunter_score": job.get("hunter_score"),
                "coverage": diagnostics.get("jd_term_coverage_percent"),
                "truth_status": str(
                    (diagnostics.get("truth_audit") or {}).get("status") or "PASS"
                ),
                "engine": "MUNSHI Native",
                "is_current": str(version["version_id"]) == current_by_job[job_id],
                "application_status": status,
                "application_id": application_id,
            }
        )
    return result


def _filter_resume_library(
    rows: list[dict[str, Any]],
    *,
    query: str,
    status: str,
    date_range: str,
) -> list[dict[str, Any]]:
    terms = [term for term in str(query or "").casefold().split() if term]
    now = datetime.now().astimezone()
    result: list[dict[str, Any]] = []
    for row in rows:
        blob = " ".join(
            str(row.get(key) or "")
            for key in ("company_name", "title", "application_status", "created_at")
        ).casefold()
        if terms and not all(term in blob for term in terms):
            continue
        if status == "current" and not row.get("is_current"):
            continue
        if status == "superseded" and row.get("is_current"):
            continue
        if status == "linked" and not row.get("application_id"):
            continue
        if date_range != "all":
            try:
                created = datetime.fromisoformat(
                    str(row.get("created_at") or "").replace("Z", "+00:00")
                )
                if created.tzinfo is None:
                    created = created.replace(tzinfo=now.tzinfo)
                days = 30 if date_range == "30d" else 90
                if (now - created.astimezone(now.tzinfo)).days > days:
                    continue
            except (TypeError, ValueError):
                continue
        result.append(row)
    return result


def _resume_library() -> None:
    rows = _resume_library_rows()
    st.markdown("### Tailored resumes")
    if not rows:
        st.info("Your generated, truth-checked resumes will appear here.")
        return

    controls = st.columns((3, 1.2, 1.2), gap="small")
    query = controls[0].text_input(
        "Search tailored resumes",
        placeholder="Search company or job title, such as Oklo…",
        key="resume_library_search",
    )
    status = controls[1].selectbox(
        "Status",
        ("all", "current", "superseded", "linked"),
        format_func=lambda value: {
            "all": "All versions",
            "current": "Current",
            "superseded": "Superseded",
            "linked": "Linked application",
        }[value],
        key="resume_library_status",
    )
    date_range = controls[2].selectbox(
        "Generated",
        ("all", "30d", "90d"),
        format_func=lambda value: {"all": "Any date", "30d": "Last 30 days", "90d": "Last 90 days"}[value],
        key="resume_library_date",
    )
    filtered = _filter_resume_library(
        rows,
        query=query,
        status=status,
        date_range=date_range,
    )
    st.caption(f"{len(filtered)} saved tailored resume{'s' if len(filtered) != 1 else ''}.")
    for row in filtered[:12]:
        version_id = str(row["version_id"])
        job_id = int(row["job_id"])
        with st.container(border=True, key=f"resume_library_{version_id}"):
            identity, metadata = st.columns((2.1, 1.4), gap="small")
            identity.markdown(f"**{row['company_name']} · {row['title']}**")
            identity.caption(
                f"v{int(row.get('version_number') or 0)} · "
                f"{row.get('created_at') or 'Date unavailable'} · "
                f"{'Current' if row.get('is_current') else 'Superseded'}"
            )
            coverage = row.get("coverage")
            metadata.caption(
                f"JD coverage: {float(coverage):.0f}%" if coverage is not None else "JD coverage: —"
            )
            metadata.caption(
                f"Truth: {row['truth_status']} · Engine: {row['engine']} · "
                f"Application: {row['application_status']}"
            )
            actions = st.columns(4, gap="small")
            if actions[0].button("Open", key=f"resume_library_open_{version_id}", use_container_width=True):
                st.session_state["native_resume_job_id"] = job_id
                st.session_state["native_resume_selected_version"] = version_id
                st.rerun()
            actions[1].button(
                "Open application",
                key=f"resume_library_application_{version_id}",
                disabled=not bool(row.get("application_id")),
                use_container_width=True,
                on_click=_open_job_view,
                args=(job_id,),
                kwargs={
                    "application": True,
                    "application_id": str(row.get("application_id") or "") or None,
                },
            )
            actions[2].button(
                "View job",
                key=f"resume_library_job_{version_id}",
                use_container_width=True,
                on_click=_open_job_view,
                args=(job_id,),
            )
            if actions[3].button(
                "Regenerate",
                key=f"resume_library_regenerate_{version_id}",
                use_container_width=True,
            ):
                st.session_state["native_resume_job_id"] = job_id
                st.session_state["native_resume_selected_version"] = version_id
                st.session_state[f"resume_regenerate_requested_{job_id}"] = True
                st.rerun()
    if len(filtered) > 12:
        st.caption("Refine the search or filters to view more saved resumes.")


def _version_label(row: dict[str, Any]) -> str:
    diagnostics = row.get("diagnostics") or {}
    mode = STYLE_LABELS.get(diagnostics.get("rewrite_mode"), "Tailored")
    return f"v{row['version_number']} · {mode}"


def _version_workspace(job_id: int, context: dict[str, Any], writer_ready: bool) -> None:
    all_versions = [
        row
        for row in resume_v5.list_versions(job_id=job_id, limit=100)
        if row.get("stage_b_bound") is True
    ]
    if not all_versions:
        return

    versions, hidden_repairs = _user_facing_versions(all_versions)
    if not versions:
        versions = [all_versions[0]]
        hidden_repairs = max(0, len(all_versions) - 1)

    st.divider()
    st.markdown("### Current resume")

    ids = [str(row["version_id"]) for row in versions]
    current_id = ids[0]
    selected = str(st.session_state.get("native_resume_selected_version") or "")
    if selected not in ids:
        selected = current_id
        st.session_state["native_resume_selected_version"] = current_id

    by_id = {str(row["version_id"]): row for row in versions}
    selected_id = selected
    viewing_prior = selected_id != current_id

    if viewing_prior:
        with st.container(key="munshi_tint_resume_prior"):
            st.warning(
                f"Viewing prior revision {_version_label(by_id[selected_id])}. "
                "Downloads and refinement below apply to this revision."
            )
            if st.button(
                "Back to current resume",
                key="resume_studio_v4_back_to_current",
                type="primary",
                use_container_width=False,
            ):
                st.session_state["native_resume_selected_version"] = current_id
                st.rerun()
    else:
        st.caption(
            f"{_version_label(by_id[current_id])} is the current user-facing resume."
        )

    prior_versions = versions[1:]
    if prior_versions or hidden_repairs:
        with st.expander(
            f"Revision history · {len(prior_versions)} prior"
            + (f" · {hidden_repairs} internal repair pass(es)" if hidden_repairs else ""),
            expanded=False,
        ):
            if prior_versions:
                prior_ids = [str(row["version_id"]) for row in prior_versions]
                history_id = st.selectbox(
                    "Prior revision",
                    prior_ids,
                    format_func=lambda value: _version_label(by_id[value]),
                    key="resume_studio_v4_history_version",
                )
                if st.button(
                    "View selected revision",
                    key="resume_studio_v4_view_history",
                    use_container_width=True,
                ):
                    st.session_state["native_resume_selected_version"] = history_id
                    st.rerun()
            else:
                st.caption("No prior user-facing revisions.")

            if hidden_repairs:
                st.caption(
                    f"{hidden_repairs} intermediate one-page repair pass(es) are hidden "
                    "from the normal resume list. They remain in the immutable audit trail."
                )

    record = resume_v5.get_version(selected_id)
    diagnostics = record.get("diagnostics") or {}
    artifact_rows, quality = _artifact_bundle(selected_id)

    metrics = st.columns(4, gap="small")
    metrics[0].metric("Revision", f"v{record['version_number']}")
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

    with st.expander("JD match details", expanded=False):
        matched = [str(value) for value in diagnostics.get("matched_jd_terms") or []]
        missing = [str(value) for value in diagnostics.get("missing_jd_terms") or []]
        st.markdown("**Matched terms**")
        st.write(", ".join(matched) if matched else "No matched-term list recorded.")
        st.markdown("**Terms still missing**")
        st.write(", ".join(missing) if missing else "No missing JD terms recorded.")
        ats = diagnostics.get("ats_readiness_estimate")
        if ats is not None:
            st.caption(f"ATS readiness estimate: {ats}")
        st.caption(
            "Writer model IDs, reasoning metadata and API-call telemetry are engineering "
            "diagnostics and are kept out of the normal Resume Studio workflow."
        )
        st.link_button(
            "Open system diagnostics",
            "?view=settings&section=advanced",
            use_container_width=False,
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
    _resume_library()

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
