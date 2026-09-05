"""Candidate-facing Phase 4–7 application preparation workspace.

This page deliberately exposes preparation intelligence without adding browser,
email, outreach, or submission authority. Expensive/persisting evaluations run
only after an explicit candidate click; ordinary page render is read-only.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.product_ui import esc, page_intro
from app.phase67_common import safe_owned_job_snapshot


def _job_options(limit: int = 250) -> list[dict[str, Any]]:
    """Return only jobs visible through the Phase 6/7 ownership contract."""
    from app.database import get_connection

    connection = get_connection()
    try:
        rows = connection.execute(
            """SELECT id FROM jobs
                 ORDER BY COALESCE(last_seen_at, first_seen_at, created_at) DESC, id DESC
                 LIMIT ?""",
            (int(limit),),
        ).fetchall()
    finally:
        connection.close()

    visible: list[dict[str, Any]] = []
    for row in rows:
        try:
            snapshot = safe_owned_job_snapshot(int(row["id"]))
        except (LookupError, ValueError):
            continue
        job = dict(snapshot["job"])
        visible.append(
            {
                "id": int(job["id"]),
                "company_name": job.get("company_name") or "Company",
                "title": job.get("title") or "Untitled role",
                "location_raw": job.get("location_raw") or "Location not recorded",
                "hunter_score": job.get("hunter_score"),
                "job_snapshot_sha256": snapshot["job_snapshot_sha256"],
            }
        )
    return visible


def _job_label(row: dict[str, Any]) -> str:
    score = ""
    if row.get("hunter_score") is not None:
        try:
            score = f" · {float(row['hunter_score']):.0f}%"
        except (TypeError, ValueError):
            score = ""
    return f"#{int(row['id'])} · {row['company_name']} · {row['title']}{score}"


def _requested_job_id() -> int | None:
    try:
        raw = str(st.query_params.get("job") or "").strip()
    except Exception:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _latest_opportunity(job_id: int) -> dict[str, Any] | None:
    from app import opportunity_intelligence_v3 as opportunity
    from app.database import get_connection
    from app.tenant_foundation import current_owner

    opportunity.ensure_schema()
    connection = get_connection()
    try:
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT evaluation_id
                 FROM opportunity_intelligence_evaluations
                WHERE tenant_id=? AND user_id=? AND job_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1""",
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    evaluation_id = str(row["evaluation_id"])
    try:
        result = opportunity.get_evaluation(evaluation_id)
        result["freshness"] = opportunity.evaluation_freshness(evaluation_id)
        return result
    except (LookupError, RuntimeError, ValueError):
        return None


def _latest_relationship(job_id: int) -> dict[str, Any] | None:
    from app import relationship_intelligence_v3 as relationship
    from app.database import get_connection
    from app.tenant_foundation import current_owner

    relationship.ensure_schema()
    connection = get_connection()
    try:
        owner = current_owner(connection)
        row = connection.execute(
            """SELECT strategy_id
                 FROM relationship_strategy_snapshots
                WHERE tenant_id=? AND user_id=? AND job_id=?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1""",
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    strategy_id = str(row["strategy_id"])
    try:
        result = relationship.get_strategy(strategy_id)
        result["freshness"] = relationship.strategy_freshness(strategy_id)
        return result
    except (LookupError, RuntimeError, ValueError):
        return None


def _resume_versions(job_id: int) -> list[dict[str, Any]]:
    from app import native_resume_service_v4 as resumes

    try:
        resumes.ensure_schema()
        return resumes.list_versions(job_id=int(job_id), limit=50)
    except Exception:
        return []


def _safe_planning_input() -> dict[str, Any]:
    from app import answer_brain_v2 as answers

    try:
        return answers.planning_input()
    except Exception:
        return {"answers": [], "excluded_answers": [], "candidate_truth_binding": None}


def _readiness(
    *,
    job_id: int,
    resume_version_id: str | None,
    opportunity_evaluation_id: str | None,
    relationship_strategy_id: str | None,
) -> dict[str, Any]:
    from app.phase47_integrity import application_preparation_readiness

    try:
        return application_preparation_readiness(
            job_id=int(job_id),
            resume_version_id=resume_version_id,
            opportunity_evaluation_id=opportunity_evaluation_id,
            relationship_strategy_id=relationship_strategy_id,
        )
    except Exception as error:
        return {
            "status": "HOLD",
            "blockers": [f"Readiness check unavailable: {error}"],
            "submission_authority": False,
            "automatic_actions_executed": False,
        }


def _render_job_header(snapshot: dict[str, Any]) -> None:
    job = snapshot["job"]
    st.markdown(
        f"""<div class="product-callout"><div>
        <strong>{esc(job.get('company_name'), 'Company')} · {esc(job.get('title'), 'Untitled role')}</strong>
        <span>{esc(job.get('location_raw'), 'Location not recorded')} · Stored job #{int(job['id'])}</span>
        </div><span class="status-chip">Evidence-bound workspace</span></div>""",
        unsafe_allow_html=True,
    )
    st.caption(
        "This workspace prepares evidence and decisions only. It cannot submit an application, send outreach, or use ATS credentials."
    )


def _render_opportunity(job_id: int, current: dict[str, Any] | None) -> dict[str, Any] | None:
    from app import opportunity_intelligence_v3 as opportunity

    st.markdown("### Opportunity intelligence")
    st.caption("Phase 6 · Evidence-backed fit, hard-policy gates, unknowns, and pursuit recommendation.")
    if not opportunity.opportunity_intelligence_enabled():
        st.warning("Opportunity Intelligence is disabled in this environment.")
        return current

    if st.button("Evaluate opportunity", key=f"phase6_evaluate_{job_id}", type="primary"):
        try:
            with st.spinner("Evaluating current Candidate Truth, job evidence, preferences, and policy…"):
                current = opportunity.evaluate_job(int(job_id))
            st.success("Opportunity evaluation saved against the current evidence snapshot.")
        except Exception as error:
            st.error(str(error))

    if not current:
        st.info("No saved Phase 6 evaluation for this job yet. Nothing runs until you select Evaluate opportunity.")
        return None

    freshness = current.get("freshness") or {}
    if current.get("evaluation_id") and not freshness:
        try:
            freshness = opportunity.evaluation_freshness(str(current["evaluation_id"]))
        except Exception:
            freshness = {}
    fresh = freshness.get("fresh") is True
    cols = st.columns(4)
    cols[0].metric("Decision", str(current.get("status") or "Unknown"))
    score = current.get("opportunity_score")
    cols[1].metric("Evidence score", "Unknown" if score is None else f"{float(score):.1f}%")
    cols[2].metric("Confidence", f"{float(current.get('score_confidence') or 0) * 100:.0f}%")
    cols[3].metric("Freshness", "Current" if fresh else "Review")
    pursuit = current.get("pursuit_strategy") or {}
    st.write(f"**Recommended pursuit:** {pursuit.get('pursuit_state') or 'Not resolved'}")
    if pursuit.get("reason"):
        st.caption(str(pursuit["reason"]))
    failures = list(current.get("hard_failures") or [])
    unknowns = list(current.get("unknowns") or [])
    if failures:
        st.error("Hard-policy blockers: " + ", ".join(failures))
    if unknowns:
        st.warning("Still unresolved: " + ", ".join(unknowns))
    if not failures and not unknowns:
        st.success("No unresolved Phase 6 policy or evidence items are recorded.")
    return current


def _render_resume(job_id: int, versions: list[dict[str, Any]]) -> str | None:
    st.markdown("### Resume for this job")
    st.caption("Phase 4 · Truth-bound and exact-job-snapshot-bound native resume versions.")
    if not versions:
        st.info("No native strengthened resume is stored for this job yet.")
        st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=False)
        return None

    labels = {
        f"v{row.get('version_number', '?')} · {str(row.get('version_id'))[:12]}…": row
        for row in versions
    }
    chosen_label = st.selectbox("Resume version", list(labels), key=f"phase4_resume_{job_id}")
    chosen = labels[chosen_label]
    truth_bound = chosen.get("candidate_truth_bound") is True
    job_bound = chosen.get("job_snapshot_bound") is True
    cols = st.columns(3)
    cols[0].metric("Candidate Truth", "Bound" if truth_bound else "Legacy / unbound")
    cols[1].metric("Job snapshot", "Bound" if job_bound else "Legacy / unbound")
    cols[2].metric("Version", f"v{chosen.get('version_number', '?')}")
    if not truth_bound or not job_bound:
        st.warning("This version cannot satisfy strengthened Phase 4 readiness until regenerated from the current job and Candidate Truth.")
    st.link_button("Open Resume Studio", "?view=resume-studio", use_container_width=False)
    return str(chosen.get("version_id") or "") or None


def _render_answers(planning: dict[str, Any]) -> None:
    st.markdown("### Application answers")
    st.caption("Phase 5 · Planner-safe answer memory. Protected and stale profile content is not shown here.")
    answers = list(planning.get("answers") or [])
    excluded = list(planning.get("excluded_answers") or [])
    ready = [row for row in answers if row.get("planning_use") == "autofill_ready"]
    context = [row for row in answers if row.get("planning_use") == "context_only"]
    cols = st.columns(3)
    cols[0].metric("Autofill-ready", len(ready))
    cols[1].metric("Context only", len(context))
    cols[2].metric("Stale excluded", len(excluded))
    if not answers and not excluded:
        st.info("No normal Answer Brain memories are stored yet. Missing evidence remains unresolved rather than guessed.")
    if answers:
        with st.expander("Review safe answer inventory", expanded=False):
            rows = []
            for answer in answers:
                rows.append(
                    {
                        "Question family": answer.get("question_family"),
                        "Question key": answer.get("question_key") or "—",
                        "Use": answer.get("planning_use"),
                        "Source": answer.get("source"),
                        "Confidence": answer.get("confidence"),
                    }
                )
            st.dataframe(rows, hide_index=True, use_container_width=True)
    if excluded:
        with st.expander("Excluded stale profile memories", expanded=False):
            st.dataframe(excluded, hide_index=True, use_container_width=True)


def _render_relationship(
    job_id: int,
    current: dict[str, Any] | None,
    opportunity: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from app import relationship_intelligence as relationship_ledger
    from app import relationship_intelligence_v3 as relationship

    st.markdown("### People and relationship intelligence")
    st.caption("Phase 7 · Stored relationship evidence only. No contact discovery, email guessing, or outreach sending.")
    if not relationship.relationship_intelligence_enabled():
        st.warning("Relationship Intelligence is disabled in this environment.")
        return current

    try:
        contacts = relationship_ledger.contacts_for_job(job_id=int(job_id))
    except Exception:
        contacts = []
    st.metric("Evidence-linked contacts", len(contacts))
    if contacts:
        with st.expander("Review linked people", expanded=False):
            st.dataframe(
                [
                    {
                        "Name": row.get("display_name"),
                        "Role": row.get("title"),
                        "Type": row.get("contact_type"),
                        "Confidence": row.get("confidence"),
                        "Recommended action": row.get("recommended_action"),
                        "Source": row.get("source"),
                    }
                    for row in contacts
                ],
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.info("No evidence-linked contacts are stored for this job. Relationship Intelligence will not invent one.")

    opportunity_id = str((opportunity or {}).get("evaluation_id") or "") or None
    if st.button("Evaluate relationship strategy", key=f"phase7_evaluate_{job_id}", disabled=not bool(contacts)):
        try:
            with st.spinner("Scoring stored relationship evidence against the current opportunity…"):
                current = relationship.evaluate_relationship_strategy(
                    int(job_id),
                    opportunity_evaluation_id=opportunity_id,
                    persist=True,
                )
            st.success("Relationship strategy saved against current evidence.")
        except Exception as error:
            st.error(str(error))

    if current:
        freshness = current.get("freshness") or {}
        if current.get("strategy_id") and not freshness:
            try:
                freshness = relationship.strategy_freshness(str(current["strategy_id"]))
            except Exception:
                freshness = {}
        strategy = current.get("combined_strategy") or current.get("strategy") or {}
        cols = st.columns(3)
        cols[0].metric("Strategy", str(strategy.get("pursuit_state") or current.get("pursuit_state") or "Review"))
        score = current.get("relationship_score")
        cols[1].metric("Relationship score", "Unknown" if score is None else f"{float(score):.1f}%")
        cols[2].metric("Freshness", "Current" if freshness.get("fresh") is True else "Review")
        unknowns = list(current.get("unknowns") or [])
        if unknowns:
            st.warning("Relationship evidence unresolved: " + ", ".join(unknowns))
    return current


def _render_readiness(
    *,
    job_id: int,
    resume_version_id: str | None,
    opportunity: dict[str, Any] | None,
    relationship: dict[str, Any] | None,
) -> None:
    st.markdown("### Application readiness")
    st.caption("Cross-phase integrity · One coherent current state across Candidate Truth, resume, answers, opportunity, and optional relationships.")
    result = _readiness(
        job_id=int(job_id),
        resume_version_id=resume_version_id,
        opportunity_evaluation_id=str((opportunity or {}).get("evaluation_id") or "") or None,
        relationship_strategy_id=str((relationship or {}).get("strategy_id") or "") or None,
    )
    status = str(result.get("status") or "HOLD")
    if status == "READY":
        st.success("READY · Current Phase 4–7 evidence is mutually consistent for application preparation.")
    else:
        st.warning("HOLD · Resolve the items below before treating this application package as ready.")
    blockers = list(result.get("blockers") or result.get("reasons") or [])
    if blockers:
        for blocker in blockers:
            st.write(f"• {blocker}")
    st.info("Preparation only · Submission authority: OFF · Automatic external actions: OFF")


def render() -> None:
    page_intro(
        "PREPARE APPLICATION",
        "One evidence-bound workspace for this job",
        "Review the opportunity, resume, safe application answers, relationship evidence, and final readiness before any future handoff.",
    )
    jobs = _job_options()
    if not jobs:
        st.info("No owned jobs are available yet. Add or discover a job first.")
        return

    by_id = {int(row["id"]): row for row in jobs}
    requested = _requested_job_id()
    default_id = requested if requested in by_id else int(jobs[0]["id"])
    labels = {_job_label(row): int(row["id"]) for row in jobs}
    default_label = next(label for label, value in labels.items() if value == default_id)
    chosen_label = st.selectbox(
        "Application job",
        list(labels),
        index=list(labels).index(default_label),
        key="phase17_application_job",
    )
    job_id = labels[chosen_label]
    try:
        st.query_params["view"] = "prepare-application"
        st.query_params["job"] = str(job_id)
    except Exception:
        pass

    snapshot = safe_owned_job_snapshot(job_id)
    _render_job_header(snapshot)

    opportunity = _latest_opportunity(job_id)
    relationship = _latest_relationship(job_id)
    resumes = _resume_versions(job_id)
    planning = _safe_planning_input()

    opportunity = _render_opportunity(job_id, opportunity)
    st.divider()
    resume_version_id = _render_resume(job_id, resumes)
    st.divider()
    _render_answers(planning)
    st.divider()
    relationship = _render_relationship(job_id, relationship, opportunity)
    st.divider()
    _render_readiness(
        job_id=job_id,
        resume_version_id=resume_version_id,
        opportunity=opportunity,
        relationship=relationship,
    )
