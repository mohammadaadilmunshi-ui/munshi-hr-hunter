"""Candidate-facing Complete Loop application workspace.

This page exposes durable Prepare progress, structured NEEDS_INPUT,
READY_FOR_REVIEW artifacts, and lifecycle / verified-receipt history while
preserving the existing evidence-bound Phase 4–7 intelligence.

Ordinary rendering is read-only. An explicit Prepare click starts preparation.
The single Approve & Submit action authorizes only the exact displayed digest;
all subsequent claim, submit, verification, and receipt work is automatic.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app.product_ui import esc, page_intro
from app.product_experience_vnext import render_application_breadcrumb, render_lifecycle_steps
from app import product_application_flow_v2 as complete_loop
from app.phase67_common import safe_owned_job_snapshot


_LIVE_PROGRESS_STATES = frozenset({
    "QUEUED",
    "RUNNING",
    "OPTIMIZING",
    "PREPARING",
    "PLAN_ACCEPTED",
    "READY_TO_APPLY",
    "PREPARING_ATS",
    "SUBMITTING",
})
_LIVE_PROGRESS_INTERVAL = "2s"


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
        "This workspace prepares the exact application package and provides the one customer review. It never sends outreach or exposes ATS credentials."
    )



def _render_application_lifecycle_guide() -> None:
    st.markdown("### What happens to every application")
    render_lifecycle_steps()
    st.info(
        "You only need to intervene when MUNSHI shows **Needs input**, "
        "**Ready for review**, or a protected security checkpoint."
    )
    st.caption(
        "These are process stages, not proof of completed submission. "
        "Only independently confirmed employer evidence counts as a verified receipt."
    )



def _render_current_action(state: str) -> None:
    normalized = str(state or "").strip().upper()
    if not normalized:
        st.info(
            "**Your action now:** none required unless you want to start this job manually. "
            "Auto Prepare may also admit an eligible job automatically."
        )
        return

    if normalized in {
        "OPTIMIZING",
        "PREPARING",
        "PLAN_ACCEPTED",
        "PREPARING_ATS",
        "READY_TO_APPLY",
    }:
        st.info(
            "**Your action now:** none. MUNSHI is preparing the package in the background."
        )
    elif normalized == "NEEDS_INPUT":
        st.warning(
            "**Your action now:** answer only the genuine missing employer question(s) shown below. "
            "MUNSHI will not guess."
        )
    elif normalized == "READY_FOR_APPROVAL":
        st.warning(
            "**Your action now:** review the completed pre-browser package once, then press "
            "**Approve & Submit** if it is correct. Chromium/ATS execution starts only after "
            "this one approval, and there is no later review or submit prompt."
        )
    elif normalized == "SUBMITTING":
        st.info(
            "**Your action now:** none. Your single approval is already recorded and MUNSHI is applying."
        )
    elif normalized in {"SUBMITTED", "VERIFIED", "AWAITING_RESPONSE"}:
        st.success(
            "**Your action now:** none. Submission/verification evidence is recorded; "
            "MUNSHI is tracking the response lifecycle."
        )
    elif normalized in {"ISSUE", "FAILED_SAFELY", "BLOCKED", "SUBMISSION_UNVERIFIED"}:
        st.error(
            "**Your action now:** review the recorded issue before retrying. "
            "Do not submit again unless MUNSHI explicitly makes retry available."
        )
    elif normalized == "CANCELLED":
        st.warning(
            "**Your action now:** this application flow is cancelled. Start a new preparation "
            "only if you still want to pursue the role."
        )
    else:
        st.info(
            f"**Current lifecycle:** {normalized.replace('_', ' ').title()}. "
            "Follow the recorded application timeline below."
        )


def _human_unresolved(value: str) -> str:
    text = str(value or '').strip()
    if text.startswith('apply.runtime_question:'):
        return 'Employer application question'
    aliases = {
        'WORK_AUTH_US': 'U.S. work authorization',
        'SPONSORSHIP': 'Future sponsorship',
        'SALARY': 'Compensation expectation',
        'RELOCATION': 'Relocation',
        'TRAVEL': 'Travel',
        'WORK_ARRANGEMENT': 'Work arrangement',
        'START_DATE': 'Start date',
    }
    return aliases.get(text, text.replace('_', ' ').title())


def _render_complete_loop(job_id: int, *, compact: bool = False) -> dict[str, Any]:
    flow = complete_loop.flow_for_job(int(job_id))
    state = str(flow.get('state') or '')

    if not compact:
        _render_current_action(state)
        st.markdown('### Application progress')
        st.caption(
            'The source of truth across package preparation, the one pre-browser customer '
            'approval, unattended ATS execution, submission verification, and CRM.'
        )

    if not state:
        st.info(
            'This job has not entered the Complete Application Loop yet. '
            'Prepare creates a durable application package and does not '
            'submit anything.'
        )
        engine = st.radio(
            'Resume engine',
            ['NATIVE', 'N8N'],
            horizontal=True,
            format_func=lambda value: (
                'MUNSHI Native' if value == 'NATIVE' else 'n8n'
            ),
            key=f'complete_loop_engine_{job_id}',
            help=(
                'MUNSHI Native uses the truth-bound Native Resume engine. '
                'n8n remains an optional resume-generation path.'
            ),
        )
        if st.button(
            'Prepare Application',
            type='primary',
            use_container_width=True,
            key=f'complete_loop_prepare_{job_id}',
        ):
            try:
                with st.spinner(
                    'Starting your evidence-bound application package…'
                ):
                    complete_loop.start_prepare_for_job(
                        int(job_id),
                        engine=engine,
                    )
            except Exception as error:
                st.error(str(error))
            else:
                st.toast(
                    'Application preparation started.',
                    icon='✅',
                )
                st.rerun()

        st.caption(
            'Preparation only · no employer submission occurs until you review '
            'the completed package and press Approve & Submit.'
        )
        return flow

    prepare = flow.get('prepare') or {}
    lifecycle = flow.get('lifecycle') or {}

    if not compact:
        metrics = st.columns(4)
        metrics[0].metric('Status', flow['display_status'])
        metrics[1].metric(
            'Progress',
            f"{int(flow.get('progress_percent') or 0)}%",
        )
        metrics[2].metric(
            'Resume engine',
            str(prepare.get('engine') or '—').title(),
        )
        metrics[3].metric(
            'Questions',
            len(flow.get('runtime_questions') or []),
        )
        st.progress(int(flow.get('progress_percent') or 0) / 100)

    tasks = list(flow.get('tasks') or [])
    if tasks and not compact:
        rows = []
        for task in tasks:
            raw_status = str(task.get('status') or '')
            rows.append(
                {
                    'Step': task.get('label'),
                    'Status': {
                        'PENDING': 'Waiting',
                        'RUNNING': 'Working',
                        'SUCCEEDED': 'Ready',
                        'NEEDS_INPUT': 'Needs input',
                        'FAILED': 'Needs attention',
                        'CANCELLED': 'Cancelled',
                    }.get(raw_status, raw_status.title()),
                    'Required': (
                        'Yes' if task.get('required') else 'Optional'
                    ),
                }
            )
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
        )

    if state == 'NEEDS_INPUT':
        st.warning(
            'MUNSHI needs information that is not safely available '
            'from Candidate Truth.'
        )
        runtime_questions = list(flow.get('runtime_questions') or [])
        if runtime_questions:
            for index, question in enumerate(runtime_questions, 1):
                with st.container(border=True):
                    st.markdown(
                        f"**{esc(question.get('question'), f'Application question {index}')}**",
                        unsafe_allow_html=True,
                    )
                    details = [
                        str(
                            question.get('category') or ''
                        ).replace('_', ' ').title(),
                        str(
                            question.get('semantic_type') or ''
                        ).replace('_', ' ').title(),
                        str(
                            question.get('sensitivity_class') or ''
                        ).replace('_', ' ').title(),
                    ]
                    st.caption(
                        ' · '.join(
                            value for value in details if value
                        )
                    )
                    if question.get('reason'):
                        st.caption(str(question['reason']))
                    if question.get('requires_user'):
                        st.info(
                            'Your answer is required. MUNSHI will not '
                            'invent or expose a stored resolution value.'
                        )
        else:
            unresolved = [
                _human_unresolved(value)
                for value in (prepare.get('unresolved') or [])
            ]
            if unresolved:
                for item in unresolved:
                    st.write(f'• {item}')
            else:
                st.caption(
                    'The runtime is synchronizing the exact employer '
                    'question metadata.'
                )

        st.caption(
            'For a paused, previously accepted Workday plan, use the exact '
            'existing-checkpoint continuation below. Other unresolved or protected '
            'questions must be completed through their authorized workflow.'
        )

    # Explicit same-session continuation is different from generic Retry:
    # it retains the accepted immutable plan, Apply session, and browser job.
    has_accepted_checkpoint_plan = False
    if (
        state in {'NEEDS_INPUT', 'FAILED_SAFELY'}
        and flow.get('execution_mandate')
        and flow.get('application_id')
    ):
        from app import application_checkpoint_continuation_v1 as checkpoint_recovery

        try:
            accepted_plan_id = checkpoint_recovery._latest_accepted_plan(
                str(flow['application_id'])
            )
            has_accepted_checkpoint_plan = True
        except (LookupError, PermissionError, RuntimeError, ValueError):
            accepted_plan_id = ''

        if has_accepted_checkpoint_plan:
            with st.expander('Continue the existing employer browser checkpoint'):
                st.caption(
                    'Use the existing Apply session and checkpoint from your runtime '
                    'diagnostics. This does not create another application, regenerate '
                    'your resume, or approve an employer submission.'
                )
                with st.form(f'checkpoint_continue_{job_id}'):
                    st.text_input('Accepted immutable plan', value=accepted_plan_id, disabled=True)
                    session_input = st.text_input(
                        'Existing Apply session ID',
                        key=f'checkpoint_session_{job_id}',
                    )
                    prepare_input = st.text_input(
                        'Existing Apply preparation job ID',
                        key=f'checkpoint_prepare_job_{job_id}',
                    )
                    checkpoint_input = st.text_input(
                        'Current Workday checkpoint ID',
                        key=f'checkpoint_current_{job_id}',
                    )
                    confirmed = st.checkbox(
                        'Continue only this existing application and stop if identity '
                        'or browser evidence has changed.',
                        key=f'checkpoint_confirm_{job_id}',
                    )
                    go = st.form_submit_button('Continue existing checkpoint')
                    finalize = st.form_submit_button('Finalize verified Workday Review')
                if go or finalize:
                    if not confirmed:
                        st.error('Confirm same-application continuation first.')
                    else:
                        try:
                            with st.spinner('Validating original Workday checkpoint…'):
                                action = (
                                    checkpoint_recovery.finalize_existing_review
                                    if finalize else
                                    checkpoint_recovery.continue_existing_checkpoint
                                )
                                outcome = action(
                                    job_id=int(job_id),
                                    plan_id=accepted_plan_id,
                                    session_id=session_input,
                                    prepare_job_id=prepare_input,
                                    checkpoint_id=checkpoint_input,
                                )
                        except (LookupError, PermissionError, RuntimeError, ValueError) as error:
                            st.error('Checkpoint not continued: ' + str(error))
                        else:
                            if finalize:
                                st.success(
                                    'Frozen Workday Review reconciled with the original '
                                    'mandate. One-use submission authority was delivered.'
                                )
                                st.caption('No employer receipt has been verified yet.')
                            else:
                                st.success(
                                    'Existing session accepted a signed replacement plan. '
                                    'Workday preparation can continue without a new application.'
                                )
                                st.caption(
                                    'Replacement plan: '
                                    + str(outcome['replacement_plan_id'])
                                    + '. No employer submission has been claimed.'
                                )
                            st.rerun()

    artifacts = flow.get('artifacts') or {}
    resume = artifacts.get('resume') or {}
    preflight = artifacts.get('preflight') or {}

    if state == 'READY_FOR_APPROVAL':
        import streamlit.components.v1 as components

        from app import application_review_card_v1 as review_card
        from app import native_resume_presentation_v1 as resume_presentation

        review = flow.get('package_review') or {}
        review_record = review
        st.markdown('### Review tailored resume')
        st.caption(
            'This is the only customer review. Check the truthful, job-specific resume; '
            'Approve & Submit then starts unattended application execution.'
        )

        job = dict(review.get('job') or safe_owned_job_snapshot(int(job_id))['job'])
        st.markdown(
            f"**{esc(job.get('title'), 'Untitled role')} · "
            f"{esc(job.get('company_name'), 'Company')}**",
            unsafe_allow_html=True,
        )

        resume_version_id = str(resume.get('resume_version_id') or '')
        card: dict[str, Any] = {}
        if resume_version_id:
            try:
                card = review_card.load_review_card(
                    resume_version_id=resume_version_id,
                    page_verification=resume.get('page_verification') or {},
                )
            except (LookupError, RuntimeError, ValueError):
                card = {}

        if card:
            score_col, keywords_col = st.columns(2, gap='small')
            score_col.metric(
                'JD match',
                f"{card['coverage_percent']:.0f}% {card['coverage_label']}",
            )
            if int(card.get('keyword_count') or 0):
                keywords_col.metric(
                    'JD terms covered',
                    f"{int(card['covered_keyword_count'])} of "
                    f"{int(card['keyword_count'])}",
                )
            else:
                keywords_col.metric(
                    'Supported requirements covered',
                    f"{int(card['represented_requirement_count'])} of "
                    f"{int(card['supportable_requirement_count'])}",
                )
            st.caption(
                'Match uses truth-supportable Stage B requirements. '
                'Unsupported claims never inflate this score.'
            )

            st.markdown('**Required skills**')
            required_rows = list(card.get('required_skills') or [])
            if required_rows:
                for item in required_rows:
                    icon = '✓' if item.get('covered') else '○'
                    st.write(f"{icon} {item.get('text')}")
            else:
                st.caption(
                    'No separate truth-supported must-have skill rows were '
                    'recorded for this job.'
                )

            st.markdown('**What changed**')
            changed_col, skills_col = st.columns(2, gap='small')
            changed_label = (
                'Bullets improved'
                if card.get('is_revision')
                else 'Tailored bullets'
            )
            skills_label = (
                'Skills added'
                if card.get('is_revision')
                else 'Job-relevant skills'
            )
            changed_col.metric(
                changed_label,
                int(card.get('changed_bullet_count') or 0),
            )
            skills_col.metric(
                skills_label,
                int(card.get('added_skill_count') or 0),
            )

            additions = list(card.get('added_skills') or [])[:6]
            if additions:
                for value in additions:
                    st.caption(f"+ {value}")

            with st.expander('See rewritten bullets', expanded=False):
                bullets = list(card.get('changed_bullets') or [])
                if bullets:
                    for bullet in bullets:
                        st.write(f"• {bullet}")
                else:
                    st.caption(
                        'No bullet text changed from the prior tailored version.'
                    )

            summary_col, edit_col = st.columns([5, 1], gap='small')
            with summary_col:
                st.markdown('**Professional summary**')
                st.write(card.get('summary') or 'No summary recorded.')
            with edit_col:
                st.write('')
                if st.button(
                    'Edit',
                    key=f'complete_loop_edit_resume_{job_id}',
                    use_container_width=True,
                    help=(
                        'Open Resume Studio. A saved revision must be '
                        're-prepared and reviewed again before submission.'
                    ),
                ):
                    st.query_params['view'] = 'resume-studio'
                    st.query_params['job'] = str(job_id)
                    st.rerun()

            st.toggle(
                'Fit to one page',
                value=bool(card.get('one_page')),
                disabled=True,
                key=f'complete_loop_one_page_{job_id}',
                help='Verified from the immutable PDF artifact.',
            )

            with st.expander('Resume preview', expanded=True):
                try:
                    preview = resume_presentation.render_document_html(
                        card['document']
                    )
                    components.html(preview, height=900, scrolling=True)
                except (RuntimeError, TypeError, ValueError):
                    st.caption('Resume preview is temporarily unavailable.')
        elif not compact:
            artifact_cols = st.columns(3)
            pdf = resume.get('pdf') or {}
            artifact_cols[0].metric(
                'Tailored resume',
                'Ready' if pdf.get('artifact_id') else 'Not recorded',
            )
            artifact_cols[1].metric(
                'Cover letter',
                (
                    'Ready'
                    if preflight.get('cover_letter_id')
                    else str(preflight.get('cover_letter_decision') or 'Not planned').replace('_', ' ').title()
                ),
            )
            artifact_cols[2].metric('ATS browser', 'Starts after approval')

        if not compact:
            with st.expander('Application details', expanded=False):
                destination = str(
                    (review.get('job') or {}).get('apply_url')
                    or (review.get('job') or {}).get('job_url')
                    or '—'
                )
                st.write(f"Exact destination: {destination}")
                policy = dict(review.get('execution_policy') or {})
                st.write(
                    'Normal employer questions: '
                    + str(policy.get('normal_runtime_questions') or 'AUTONOMOUS')
                )
                st.write(
                    'Account creation: '
                    + str(policy.get('account_creation') or 'AUTONOMOUS')
                )
                st.write(
                    'Email verification: '
                    + str(policy.get('email_verification') or 'AUTONOMOUS')
                )
                checkpoints = list(
                    policy.get('protected_human_checkpoints') or []
                )
                if checkpoints:
                    st.write(
                        'Protected human checkpoints only: '
                        + ', '.join(
                            value.replace('_', ' ').title()
                            for value in checkpoints
                        )
                    )
                answers = dict(review.get('application_answers') or {})
                reusable = list(
                    answers.get('reusable_answer_metadata') or []
                )
                st.caption(
                    f"{len(reusable)} reusable answer record(s) are bound to the current "
                    'Candidate Truth profile. Protected values are never exposed here.'
                )
                st.caption(
                    'The later browser/form digest is an internal integrity snapshot only. '
                    'It cannot create another customer review or approval prompt.'
                )

        if not review_record:
            st.warning(
                'The immutable pre-browser package review is still synchronizing. '
                'ATS execution remains blocked.'
            )
        else:
            skip_col, submit_col = st.columns([1, 2], gap='small')
            with skip_col:
                if st.button(
                    'Skip',
                    key=f'complete_loop_skip_review_{job_id}',
                    use_container_width=True,
                ):
                    st.toast('Package approval left pending. ATS execution did not start.')
                    st.query_params['view'] = 'jobs'
                    try:
                        del st.query_params['job']
                    except KeyError:
                        pass
                    st.rerun()
            with submit_col:
                if st.button(
                    'Approve & Submit',
                    type='primary',
                    use_container_width=True,
                    key=f'complete_loop_approve_submit_{job_id}',
                ):
                    try:
                        with st.spinner('Recording package approval…'):
                            complete_loop.approve_and_submit_for_job(
                                int(job_id)
                            )
                    except Exception as error:
                        st.error(
                            'AutoApply could not start safely: ' + str(error)
                        )
                    else:
                        st.toast(
                            'Approval recorded. Browser preparation is pending.',
                            icon='✅',
                        )
                        st.rerun()
        st.caption(
            'No second review follows. Normal employer questions and submission '
            'verification continue automatically; protected checkpoints remain protected.'
        )

    if state == 'SUBMITTING':
        st.info(
            'Applying now. Your single approval is recorded and the one-use '
            'canonical authority is being claimed. Do not approve again.'
        )

    if state == 'SUBMITTED':
        receipt = flow.get('production_receipt') or {}
        st.success('Submitted. Verified employer-facing evidence is recorded.')
        st.caption(
            f"Receipt {receipt.get('receipt_id') or 'recorded'} · "
            f"{receipt.get('verified_at') or 'verification time recorded'}"
        )

    if state in {'FAILED_SAFELY', 'BLOCKED', 'CANCELLED'}:
        st.warning(
            'Preparation stopped without claiming submission success. '
            + (
                'Continue the accepted checkpoint above; do not create a second '
                'preparation operation.'
                if has_accepted_checkpoint_plan
                else 'You can retry after reviewing the recorded issue.'
            )
        )
        if not has_accepted_checkpoint_plan and st.button(
            'Retry preparation',
            type='primary',
            key=f'complete_loop_retry_{job_id}',
            use_container_width=True,
        ):
            try:
                complete_loop.start_prepare_for_job(
                    int(job_id),
                    engine=str(prepare.get('engine') or 'NATIVE'),
                )
            except Exception as error:
                st.error(str(error))
            else:
                st.rerun()

    if lifecycle:
        st.markdown('#### Application timeline')
        timeline = list(lifecycle.get('timeline') or [])
        if timeline:
            rows = []
            for item in timeline:
                raw_state = str(item.get('to_state') or '')
                rows.append(
                    {
                        'Stage': complete_loop.STATE_LABELS.get(
                            raw_state,
                            raw_state.replace('_', ' ').title(),
                        ),
                        'When': (
                            item.get('occurred_at')
                            or item.get('created_at')
                        ),
                        'Reason': item.get('reason') or '',
                    }
                )
            st.dataframe(
                rows,
                hide_index=True,
                use_container_width=True,
            )

        receipt = lifecycle.get('receipt')
        if receipt:
            st.success('✓ Submission independently verified')
            receipt_cols = st.columns(3)
            receipt_cols[0].metric(
                'Provider',
                str(receipt.get('provider') or '—'),
            )
            receipt_cols[1].metric(
                'Application ID',
                str(
                    receipt.get('provider_application_id') or '—'
                ),
            )
            receipt_cols[2].metric(
                'Verification',
                str(
                    receipt.get('verification_status') or '—'
                ),
            )
            with st.expander(
                'Verified submission receipt',
                expanded=False,
            ):
                st.json(
                    {
                        'receipt_id': receipt.get('receipt_id'),
                        'receipt_digest': receipt.get(
                            'receipt_digest'
                        ),
                        'submitted_at': receipt.get('submitted_at'),
                        'verified_at': receipt.get('verified_at'),
                        'resume_sha256': receipt.get('resume_sha256'),
                        'cover_letter_sha256': receipt.get(
                            'cover_letter_sha256'
                        ),
                    }
                )

    return flow


def _live_progress_state(flow: dict[str, Any]) -> bool:
    return str(flow.get("state") or "").strip().upper() in _LIVE_PROGRESS_STATES


def _render_complete_loop_live(job_id: int) -> dict[str, Any]:
    """Refresh only the durable Complete Loop panel while background work is active."""
    initial = complete_loop.flow_for_job(int(job_id))
    run_every = _LIVE_PROGRESS_INTERVAL if _live_progress_state(initial) else None

    @st.fragment(run_every=run_every)
    def _fragment() -> dict[str, Any]:
        flow = _render_complete_loop(int(job_id))
        if run_every is not None:
            st.caption(
                "Live progress · updates automatically while MUNSHI is working."
            )
            if not _live_progress_state(flow):
                # Re-render the whole page once so the periodic fragment stops as
                # soon as user action or a terminal state is reached.
                st.rerun(scope="app")
        return flow

    return _fragment()



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
    st.info("Readiness evidence only · the sole Approve & Submit action is shown above when the exact package is ready")


def render() -> None:
    page_intro(
        "APPLICATIONS",
        "Prepare, resolve, review, and track one application",
        "MUNSHI builds the package in the background, surfaces only genuine missing information, and keeps submission verification separate from preparation.",
    )
    _render_application_lifecycle_guide()
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

    notice = st.session_state.pop("application_workspace_notice", None)
    if isinstance(notice, dict) and str(notice.get("message") or "").strip():
        tone = str(notice.get("tone") or "info")
        renderer = {
            "success": st.success,
            "warning": st.warning,
            "error": st.error,
            "info": st.info,
        }.get(tone, st.info)
        renderer(str(notice["message"]))

    try:
        st.query_params["view"] = "prepare-application"
        st.query_params["job"] = str(job_id)
    except Exception:
        pass

    snapshot = safe_owned_job_snapshot(job_id)
    render_application_breadcrumb(job_id)
    _render_job_header(snapshot)
    _render_complete_loop_live(job_id)
    st.divider()

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
