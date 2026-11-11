'''Customer-facing Complete Loop read/action model.

Ordinary reads are read-only. They translate durable Prepare and lifecycle
authority into candidate-safe UI state.

An explicit Prepare click may create a durable Prepare operation. It does not
open a browser, call a provider, send email, approve Final Review, submit an
application, or mutate n8n.

Runtime NEEDS_INPUT projection deliberately excludes stored resolution values.
'''
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.database import get_connection
from app.phase67_common import safe_owned_job_snapshot
from app.phase8_application_truth import current_application_truth_projection
from app.tenant_foundation import current_owner


PREPARE_ACTIVE = frozenset(
    {'QUEUED', 'RUNNING', 'NEEDS_INPUT', 'READY_FOR_REVIEW'}
)
PREPARE_RETRYABLE_TERMINAL = frozenset({'FAILED_SAFELY', 'CANCELLED'})

POST_PREPARE_LIFECYCLE = frozenset(
    {
        'READY_TO_SUBMIT',
        'SUBMITTING',
        'SUBMITTED',
        'SUBMISSION_UNVERIFIED',
        'VERIFIED',
        'AWAITING_RESPONSE',
        'ASSESSMENT',
        'INTERVIEW',
        'OFFER',
        'REJECTED',
        'WITHDRAWN',
    }
)

TASK_LABELS = {
    'RESUME_PREPARE': 'Tailored resume',
    'COVER_LETTER_DECISION': 'Cover letter',
    'ANSWER_RESOLUTION': 'Application answers',
    'RECRUITER_DISCOVERY': 'People context',
    'APPLICATION_PREFLIGHT': 'Application checks',
    'ATS_FORM_PREPARATION': 'Application form',
}

STATE_LABELS = {
    'QUEUED': 'Queued',
    'RUNNING': 'Preparing',
    'NEEDS_INPUT': 'Needs input',
    'READY_FOR_REVIEW': 'Ready for review',
    'FAILED_SAFELY': 'Needs attention',
    'CANCELLED': 'Cancelled',
    'DISCOVERED': 'Discovered',
    'SCORED': 'Scored',
    'PURSUIT_APPROVED': 'Approved to pursue',
    'PREPARING': 'Preparing',
    'READY_TO_APPLY': 'Package ready',
    'PLAN_ACCEPTED': 'Application plan accepted',
    'PREPARING_ATS': 'Preparing application',
    'READY_TO_SUBMIT': 'Ready to submit',
    'SUBMITTING': 'Submitting',
    'SUBMITTED': 'Submitted',
    'SUBMISSION_UNVERIFIED': 'Submission needs verification',
    'VERIFIED': 'Submission verified',
    'AWAITING_RESPONSE': 'Awaiting response',
    'ASSESSMENT': 'Assessment',
    'INTERVIEW': 'Interview',
    'OFFER': 'Offer',
    'REJECTED': 'Closed',
    'WITHDRAWN': 'Withdrawn',
    'BLOCKED': 'Blocked',
}


def _table_exists(connection: Any, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (str(table),),
        ).fetchone()
        is not None
    )


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ''))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _public_task(row: Mapping[str, Any]) -> dict[str, Any]:
    output = _json(row.get('output_json'), {})
    unresolved = _json(row.get('unresolved_json'), [])
    if not isinstance(output, dict):
        output = {}
    if not isinstance(unresolved, list):
        unresolved = []
    task_kind = str(row.get('task_kind') or '')
    return {
        'task_id': str(row.get('task_id') or ''),
        'task_kind': task_kind,
        'label': TASK_LABELS.get(
            task_kind,
            task_kind.replace('_', ' ').title(),
        ),
        'status': str(row.get('status') or ''),
        'required': bool(int(row.get('required') or 0)),
        'attempts': int(row.get('attempts') or 0),
        'output': output,
        'unresolved': [
            str(value) for value in unresolved if str(value).strip()
        ],
        'error_code': row.get('error_code'),
        'error_message': row.get('error_message'),
        'updated_at': row.get('updated_at'),
    }


def _latest_prepare(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        if not _table_exists(connection, 'prepare_operations_v1'):
            return None
        owner = current_owner(connection)
        row = connection.execute(
            '''SELECT *
                 FROM prepare_operations_v1
                WHERE tenant_id=? AND user_id=? AND job_id=?
                ORDER BY datetime(updated_at) DESC, rowid DESC
                LIMIT 1''',
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
        if row is None:
            return None

        operation = dict(row)
        tasks: list[dict[str, Any]] = []
        if _table_exists(connection, 'prepare_tasks_v1'):
            tasks = [
                _public_task(dict(item))
                for item in connection.execute(
                    '''SELECT *
                         FROM prepare_tasks_v1
                        WHERE tenant_id=? AND user_id=? AND operation_id=?
                        ORDER BY created_at, task_kind''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        str(row['operation_id']),
                    ),
                ).fetchall()
            ]
        operation['tasks'] = tasks
        operation['unresolved'] = [
            str(value)
            for value in _json(operation.get('unresolved_json'), [])
            if str(value).strip()
        ]
        return operation
    finally:
        connection.close()


def _latest_lifecycle(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        if not _table_exists(connection, 'application_lifecycle_records'):
            return None
        owner = current_owner(connection)
        row = connection.execute(
            '''SELECT a.*, j.company_name, j.title AS job_title,
                      j.location_raw, j.apply_url
                 FROM application_lifecycle_records AS a
                 JOIN jobs AS j ON j.id=a.job_id
                WHERE a.tenant_id=? AND a.user_id=? AND a.job_id=?
                ORDER BY datetime(a.updated_at) DESC, a.rowid DESC
                LIMIT 1''',
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
        if row is None:
            return None

        result = dict(row)
        timeline: list[dict[str, Any]] = []
        if _table_exists(connection, 'application_lifecycle_history_v1'):
            timeline = [
                dict(item)
                for item in connection.execute(
                    '''SELECT event_id, from_state, to_state, state_version,
                              source, reason, occurred_at, created_at
                         FROM application_lifecycle_history_v1
                        WHERE tenant_id=? AND user_id=? AND application_id=?
                        ORDER BY state_version ASC, created_at ASC''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        str(result['application_id']),
                    ),
                ).fetchall()
            ]
        result['timeline'] = timeline

        receipt = None
        receipt_id = str(result.get('verified_receipt_id') or '')
        if receipt_id and _table_exists(
            connection, 'verified_submission_receipts_v1'
        ):
            receipt_row = connection.execute(
                '''SELECT receipt_id, receipt_digest, provider,
                          provider_application_id, verification_status,
                          submitted_at, verified_at, resume_sha256,
                          cover_letter_sha256
                     FROM verified_submission_receipts_v1
                    WHERE tenant_id=? AND user_id=? AND receipt_id=?''',
                (owner.tenant_id, owner.user_id, receipt_id),
            ).fetchone()
            receipt = dict(receipt_row) if receipt_row else None
        result['receipt'] = receipt
        return result
    finally:
        connection.close()


def _required_progress(tasks: list[dict[str, Any]]) -> int:
    required = [task for task in tasks if task.get('required')]
    if not required:
        return 0
    points = 0.0
    for task in required:
        status = str(task.get('status') or '')
        if status == 'SUCCEEDED':
            points += 1.0
        elif status == 'RUNNING':
            points += 0.5
        elif status == 'NEEDS_INPUT':
            points += 0.65
    return max(0, min(100, round(100 * points / len(required))))


def _runtime_questions(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    for task in tasks:
        output = task.get('output') or {}
        for raw in output.get('runtime_resolution_tasks') or []:
            if not isinstance(raw, Mapping):
                continue
            task_id = str(raw.get('task_id') or '')
            if task_id and task_id in seen:
                continue
            if task_id:
                seen.add(task_id)

            # Deliberately whitelist safe metadata only. Resolution values,
            # evidence refs, source refs and credentials are not projected.
            result.append(
                {
                    'task_id': task_id,
                    'question_id': raw.get('question_id'),
                    'question': str(raw.get('question') or '').strip(),
                    'semantic_type': raw.get('semantic_type'),
                    'category': raw.get('category'),
                    'sensitivity_class': raw.get('sensitivity_class'),
                    'risk_level': raw.get('risk_level'),
                    'requires_user': bool(raw.get('requires_user')),
                    'reason': raw.get('reason'),
                }
            )
    return result


def _artifact_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    resume: dict[str, Any] | None = None
    cover: dict[str, Any] | None = None
    plan: dict[str, Any] | None = None

    for task in tasks:
        output = task.get('output') or {}
        kind = str(task.get('task_kind') or '')
        if kind == 'RESUME_PREPARE' and output:
            resume = {
                'resume_version_id': output.get('resume_version_id'),
                'engine': output.get('engine'),
                'pdf': output.get('pdf'),
                'docx': output.get('docx'),
                'page_verification': output.get('page_verification'),
            }
        elif kind == 'ATS_FORM_PREPARATION' and output:
            cover = {
                'decision': output.get('cover_letter_decision'),
                'cover_letter_id': output.get('cover_letter_id'),
                'artifact_sha256': output.get(
                    'cover_letter_artifact_sha256'
                ),
                'page_count': output.get('cover_letter_page_count'),
            }
            plan = {
                'plan_id': output.get('plan_id'),
                'plan_digest': output.get('plan_digest'),
                'provider': output.get('provider'),
                'apply_prepare_state': output.get(
                    'apply_prepare_state'
                ),
                'browser_preparation_completed': output.get(
                    'browser_preparation_completed'
                ),
            }

    return {
        'resume': resume,
        'cover_letter': cover,
        'plan': plan,
    }


def _effective_state(
    prepare: dict[str, Any] | None,
    lifecycle: dict[str, Any] | None,
) -> tuple[str, str]:
    lifecycle_state = str((lifecycle or {}).get('state') or '')
    prepare_state = str((prepare or {}).get('status') or '')

    if lifecycle_state in POST_PREPARE_LIFECYCLE:
        return lifecycle_state, 'lifecycle'
    if prepare_state:
        return prepare_state, 'prepare'
    if lifecycle_state:
        return lifecycle_state, 'lifecycle'
    return '', 'none'


def flow_for_job(job_id: int) -> dict[str, Any]:
    prepare = _latest_prepare(int(job_id))
    lifecycle = _latest_lifecycle(int(job_id))
    state, authority = _effective_state(prepare, lifecycle)
    tasks = list((prepare or {}).get('tasks') or [])
    progress = _required_progress(tasks)

    if state in {
        'READY_FOR_REVIEW',
        'READY_TO_SUBMIT',
        'SUBMITTING',
        'SUBMITTED',
        'SUBMISSION_UNVERIFIED',
        'VERIFIED',
        'AWAITING_RESPONSE',
        'ASSESSMENT',
        'INTERVIEW',
        'OFFER',
        'REJECTED',
        'WITHDRAWN',
    }:
        progress = 100

    if not state:
        cta = 'prepare'
        cta_label = 'Prepare'
    elif state in {'FAILED_SAFELY', 'CANCELLED', 'BLOCKED'}:
        cta = 'prepare'
        cta_label = 'Retry prepare'
    elif state in {
        'AWAITING_RESPONSE',
        'ASSESSMENT',
        'INTERVIEW',
        'OFFER',
        'REJECTED',
        'WITHDRAWN',
    }:
        cta = 'tracker'
        cta_label = STATE_LABELS.get(state, state.title())
    else:
        cta = 'workspace'
        cta_label = (
            'Review'
            if state in {'READY_FOR_REVIEW', 'READY_TO_SUBMIT'}
            else STATE_LABELS.get(
                state, state.replace('_', ' ').title()
            )
        )

    return {
        'job_id': int(job_id),
        'state': state,
        'authority': authority,
        'display_status': STATE_LABELS.get(
            state,
            state.replace('_', ' ').title() if state else 'Not started',
        ),
        'progress_percent': progress,
        'prepare': prepare,
        'lifecycle': lifecycle,
        'tasks': tasks,
        'runtime_questions': _runtime_questions(tasks),
        'artifacts': _artifact_summary(tasks),
        'cta': cta,
        'cta_label': cta_label,
        'submission_authority': False,
    }


def _stable_application_id(owner: Any, job_id: int) -> str:
    raw = (
        f'{owner.tenant_id}:{owner.user_id}:{int(job_id)}:'
        'product-complete-loop-v2'
    )
    return (
        'application-'
        + hashlib.sha256(raw.encode('utf-8')).hexdigest()[:32]
    )


def start_prepare_for_job(
    job_id: int,
    *,
    engine: str = 'NATIVE',
) -> dict[str, Any]:
    '''Start or replay durable preparation after an explicit candidate click.'''
    from app import prepare_orchestrator_v1 as prepare

    resolved_engine = str(engine or 'NATIVE').strip().upper()
    if resolved_engine not in {'NATIVE', 'N8N'}:
        raise ValueError('Prepare engine must be Native or n8n.')

    existing = _latest_prepare(int(job_id))
    if existing and str(existing.get('status') or '') in PREPARE_ACTIVE:
        result = flow_for_job(int(job_id))
        result['replayed'] = True
        return result

    snapshot = safe_owned_job_snapshot(int(job_id))
    truth = current_application_truth_projection(job_id=int(job_id))
    job_digest = str(snapshot.get('job_snapshot_sha256') or '')
    candidate_digest = str(
        (truth.get('candidate_profile_binding') or {}).get(
            'profile_digest'
        )
        or ''
    )
    if len(job_digest) != 64 or len(candidate_digest) != 64:
        raise ValueError(
            'Candidate Truth and current job evidence must be complete '
            'before Prepare.'
        )

    connection = get_connection()
    try:
        owner = current_owner(connection)
    finally:
        connection.close()

    lifecycle = _latest_lifecycle(int(job_id))
    application_id = (
        str(lifecycle.get('application_id'))
        if lifecycle and lifecycle.get('application_id')
        else _stable_application_id(owner, int(job_id))
    )

    retry_marker = (
        str(existing.get('operation_id'))
        if existing
        and str(existing.get('status') or '') in PREPARE_RETRYABLE_TERMINAL
        else 'initial'
    )
    raw_key = (
        f'{application_id}:{int(job_id)}:{resolved_engine}:'
        f'{job_digest}:{candidate_digest}:{retry_marker}:product-ux-v2'
    )
    idempotency_key = (
        'product-prepare-'
        + hashlib.sha256(raw_key.encode('utf-8')).hexdigest()[:40]
    )

    prepare.start_prepare(
        application_id=application_id,
        job_id=int(job_id),
        engine=resolved_engine,
        idempotency_key=idempotency_key,
    )
    result = flow_for_job(int(job_id))
    result['replayed'] = False
    return result


def pipeline_rows(limit: int = 250) -> list[dict[str, Any]]:
    '''Lifecycle-first candidate pipeline without requiring an n8n result row.'''
    connection = get_connection()
    try:
        owner = current_owner(connection)
        candidate_ids: list[int] = []

        if _table_exists(connection, 'prepare_operations_v1'):
            candidate_ids.extend(
                int(row['job_id'])
                for row in connection.execute(
                    '''SELECT job_id, MAX(updated_at) AS updated
                         FROM prepare_operations_v1
                        WHERE tenant_id=? AND user_id=?
                        GROUP BY job_id
                        ORDER BY datetime(updated) DESC
                        LIMIT ?''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        int(limit),
                    ),
                ).fetchall()
            )

        if _table_exists(connection, 'application_lifecycle_records'):
            candidate_ids.extend(
                int(row['job_id'])
                for row in connection.execute(
                    '''SELECT job_id, MAX(updated_at) AS updated
                         FROM application_lifecycle_records
                        WHERE tenant_id=? AND user_id=?
                        GROUP BY job_id
                        ORDER BY datetime(updated) DESC
                        LIMIT ?''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        int(limit),
                    ),
                ).fetchall()
            )

        unique_ids = list(dict.fromkeys(candidate_ids))[: int(limit)]
        result: list[dict[str, Any]] = []

        for job_id in unique_ids:
            job = connection.execute(
                '''SELECT id, company_name, title, hunter_score, apply_url
                     FROM jobs WHERE id=?''',
                (int(job_id),),
            ).fetchone()
            if job is None:
                continue

            state = flow_for_job(int(job_id))
            prepare = state.get('prepare') or {}
            lifecycle = state.get('lifecycle') or {}
            result.append(
                {
                    **dict(job),
                    'job_id': int(job_id),
                    'display_status': state['display_status'],
                    'lifecycle_state': lifecycle.get('state'),
                    'prepare_status': prepare.get('status'),
                    'progress_percent': state['progress_percent'],
                    'application_id': (
                        lifecycle.get('application_id')
                        or prepare.get('application_id')
                    ),
                    'updated_at': (
                        lifecycle.get('updated_at')
                        or prepare.get('updated_at')
                    ),
                    'status_evidence': (
                        f"lifecycle={lifecycle.get('state')}"
                        if lifecycle.get('state')
                        else f"prepare={prepare.get('status')}"
                    ),
                    'runtime_question_count': len(
                        state['runtime_questions']
                    ),
                    'verified_receipt_id': lifecycle.get(
                        'verified_receipt_id'
                    ),
                    'submission_authority': False,
                }
            )
        return result
    finally:
        connection.close()
