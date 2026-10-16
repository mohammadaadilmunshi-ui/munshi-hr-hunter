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
import logging
from collections.abc import Mapping
from typing import Any

from app.database import get_connection
from app.phase67_common import safe_owned_job_snapshot
from app.phase8_application_truth import current_application_truth_projection
from app.tenant_foundation import current_owner, current_owner_readonly


_LOGGER = logging.getLogger(__name__)


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
    'NEEDS_INPUT': 'Action required',
    'READY_FOR_APPROVAL': 'Needs review',
    'READY_FOR_REVIEW': 'Needs review',
    'FAILED_SAFELY': 'Needs attention',
    'CANCELLED': 'Cancelled',
    'DISCOVERED': 'Discovered',
    'SCORED': 'Scored',
    'PURSUIT_APPROVED': 'Approved to pursue',
    'PREPARING': 'Preparing',
    'READY_TO_APPLY': 'Package ready',
    'PLAN_ACCEPTED': 'Application plan accepted',
    'PREPARING_ATS': 'Preparing application',
    'READY_TO_SUBMIT': 'Applying now',
    'SUBMITTING': 'Applying now',
    'SUBMITTED': 'Submitted',
    'SUBMISSION_UNVERIFIED': 'Submission needs verification',
    'VERIFIED': 'Submitted',
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
        'lease_expires_at': row.get('lease_expires_at'),
        'updated_at': row.get('updated_at'),
    }


def _latest_prepare(job_id: int) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        if not _table_exists(connection, 'prepare_operations_v1'):
            return None
        owner = current_owner_readonly(connection)
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
        owner = current_owner_readonly(connection)
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


_PROTECTED_HUMAN_CHECKPOINT_TOKENS = frozenset(
    {
        "CAPTCHA",
        "SMS_MFA",
        "SMS_OTP",
        "PASSKEY",
        "BIOMETRIC",
        "IDENTITY",
        "IDENTITY_VERIFICATION",
    }
)


def _protected_human_checkpoint(
    runtime_questions: list[dict[str, Any]],
) -> bool:
    for question in runtime_questions:
        values = {
            str(question.get("category") or "").strip().upper(),
            str(question.get("semantic_type") or "").strip().upper(),
        }
        if values & _PROTECTED_HUMAN_CHECKPOINT_TOKENS:
            return True
    return False


def _artifact_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    resume: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None
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
        elif kind == 'APPLICATION_PREFLIGHT' and output:
            preflight = {
                'preflight_package_id': output.get('preflight_package_id'),
                'preflight_package_digest': output.get('preflight_package_digest'),
                'preflight_status': output.get('preflight_status'),
                'resume_version_id': output.get('resume_version_id'),
                'resume_pdf_sha256': output.get('resume_pdf_sha256'),
                'cover_letter_decision': output.get('cover_letter_decision'),
                'cover_letter_id': output.get('cover_letter_id'),
                'cover_letter_artifact_sha256': output.get(
                    'cover_letter_artifact_sha256'
                ),
                'cover_letter_page_count': output.get('cover_letter_page_count'),
                'single_approval_package_ready': output.get(
                    'single_approval_package_ready'
                ),
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
                'apply_session_id': output.get('apply_session_id'),
                'customer_review_id': output.get('customer_review_id'),
                'customer_review_digest': output.get(
                    'customer_review_digest'
                ),
                'prepared_package_digest': output.get(
                    'prepared_package_digest'
                ),
                'browser_form_digest': output.get(
                    'browser_form_digest'
                ),
            }

    return {
        'resume': resume,
        'preflight': preflight,
        'cover_letter': cover,
        'plan': plan,
    }


def _package_review(
    preflight: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    package_id = str((preflight or {}).get('preflight_package_id') or '')
    if not package_id or (preflight or {}).get('single_approval_package_ready') is not True:
        return None
    try:
        from app import application_execution_mandate_v1 as mandate_v1

        cover_letter = None
        if str((preflight or {}).get('cover_letter_id') or ''):
            cover_letter = {
                'cover_letter_id': str(preflight['cover_letter_id']),
                'artifact_sha256': str(
                    preflight.get('cover_letter_artifact_sha256') or ''
                ),
                'page_count': int(preflight.get('cover_letter_page_count') or 0),
            }
        return mandate_v1.package_review(
            preflight_package_id=package_id,
            cover_letter_decision=str(
                (preflight or {}).get('cover_letter_decision') or 'UNDETERMINED'
            ),
            cover_letter=cover_letter,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError):
        return None


def _execution_mandate(
    preflight: Mapping[str, Any] | None,
    *,
    summary: bool = False,
) -> dict[str, Any] | None:
    package_id = str((preflight or {}).get('preflight_package_id') or '')
    if not package_id:
        return None
    try:
        from app import application_execution_mandate_v1 as mandate_v1

        if summary:
            return mandate_v1.current_summary_for_preflight(package_id)
        return mandate_v1.current_for_preflight(package_id)
    except (LookupError, PermissionError, RuntimeError, ValueError):
        return None


def _customer_review(plan: Mapping[str, Any] | None) -> dict[str, Any] | None:
    review_id = str((plan or {}).get('customer_review_id') or '')
    if not review_id:
        return None
    try:
        from app import application_review_v2

        return application_review_v2.get_review(review_id)
    except (LookupError, PermissionError, RuntimeError, ValueError):
        return None


def _production_receipt(application_id: str | None) -> dict[str, Any] | None:
    if not application_id:
        return None
    connection = get_connection()
    try:
        if not _table_exists(connection, 'production_submission_receipts_v1'):
            return None
        owner = current_owner_readonly(connection)
        row = connection.execute(
            """SELECT receipt_id,provider,provider_application_id,target_url,
                      verification_method,verified_at,receipt_digest
                 FROM production_submission_receipts_v1
                WHERE tenant_id=? AND user_id=? AND application_id=?
                ORDER BY datetime(verified_at) DESC,rowid DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, str(application_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def _provider_route(job_id: int) -> dict[str, Any] | None:
    """Read the shared Native/Tsenta ownership projection without exposing IDs."""
    connection = get_connection()
    try:
        if not _table_exists(connection, 'application_provider_routes_v1'):
            return None
        owner = current_owner_readonly(connection)
        row = connection.execute(
            '''SELECT route_id,application_id,job_id,employer_url_sha256,
                      destination_evidence,provider,state,preflight_package_id,
                      package_digest,candidate_profile_digest,resume_sha256,
                      attempt_count,max_attempts,next_attempt_at,last_error_code,
                      receipt_method,receipt_evidence_digest,
                      provider_submitted_at,employer_confirmed_at,
                      updated_at
                 FROM application_provider_routes_v1
                WHERE tenant_id=? AND user_id=? AND job_id=?
                ORDER BY datetime(updated_at) DESC,rowid DESC LIMIT 1''',
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


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


def flow_for_job(job_id: int, *, summary: bool = False) -> dict[str, Any]:
    prepare = _latest_prepare(int(job_id))
    lifecycle = _latest_lifecycle(int(job_id))
    provider_route = _provider_route(int(job_id))
    state, authority = _effective_state(prepare, lifecycle)
    tasks = list((prepare or {}).get('tasks') or [])
    runtime_questions = _runtime_questions(tasks)
    artifacts = _artifact_summary(tasks)
    preflight = artifacts.get('preflight') or {}
    if summary:
        package_review = (
            {'summary_projection': True}
            if str(preflight.get('preflight_package_id') or '')
            and preflight.get('single_approval_package_ready') is True
            else None
        )
    else:
        package_review = _package_review(preflight)
    execution_mandate = _execution_mandate(
        artifacts.get('preflight'),
        summary=summary,
    )
    # The post-browser review is an INTERNAL integrity snapshot. It is never
    # projected as a second customer-facing review after the execution mandate.
    internal_review = None if summary else _customer_review(artifacts.get('plan'))
    application_id = (
        str((lifecycle or {}).get('application_id') or '')
        or str((prepare or {}).get('application_id') or '')
    )
    production_receipt = _production_receipt(application_id)
    if production_receipt is not None:
        state, authority = 'SUBMITTED', 'verified_production_receipt'
    elif internal_review is not None and internal_review.get('approval_consumed'):
        state, authority = 'SUBMITTING', 'one_use_submit_authorization'
    else:
        preflight_task = next(
            (task for task in tasks if task.get('task_kind') == 'APPLICATION_PREFLIGHT'),
            None,
        )
        ats_task = next(
            (task for task in tasks if task.get('task_kind') == 'ATS_FORM_PREPARATION'),
            None,
        )
        if (
            execution_mandate is None
            and package_review is not None
            and preflight_task is not None
            and str(preflight_task.get('status') or '') == 'SUCCEEDED'
            and ats_task is not None
            and str(ats_task.get('status') or '') == 'PENDING'
        ):
            state, authority = 'READY_FOR_APPROVAL', 'single_customer_approval_required'
        elif execution_mandate is not None and state == 'NEEDS_INPUT':
            if _protected_human_checkpoint(runtime_questions):
                state, authority = 'NEEDS_INPUT', 'protected_human_checkpoint'
            else:
                # NORMAL runtime questions are still inside the authorized
                # application attempt. Hunter/Universal Brain owns semantic
                # answerability and may create an immutable replacement plan;
                # do not project the resumable Apply checkpoint as terminal.
                state, authority = 'NEEDS_INPUT', 'runtime_resolution_in_progress'
        elif execution_mandate is not None and state in {
            'RUNNING', 'READY_FOR_REVIEW', 'READY_TO_SUBMIT'
        }:
            state, authority = 'SUBMITTING', 'single_customer_approval_recorded'
    progress = _required_progress(tasks)

    if state in {
        'READY_FOR_APPROVAL',
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
        cta, cta_label = 'prepare', 'Prepare'
    elif state in {'QUEUED', 'RUNNING', 'PREPARING', 'PREPARING_ATS'}:
        cta, cta_label = 'workspace', 'Preparing'
    elif state in {'READY_FOR_APPROVAL', 'READY_FOR_REVIEW'}:
        cta, cta_label = 'workspace', 'Review resume'
    elif state == 'NEEDS_INPUT':
        if authority == 'protected_human_checkpoint':
            cta, cta_label = 'workspace', 'Complete checkpoint'
        elif authority == 'runtime_resolution_in_progress':
            cta, cta_label = 'workspace', 'Applying now'
        else:
            cta, cta_label = 'workspace', 'Fix issue'
    elif state in {'FAILED_SAFELY', 'CANCELLED', 'BLOCKED'}:
        cta, cta_label = 'workspace', 'Fix issue'
    elif state in {'READY_TO_SUBMIT', 'SUBMITTING'}:
        cta, cta_label = 'workspace', 'Applying now'
    elif state in {'SUBMITTED', 'VERIFIED', 'AWAITING_RESPONSE'}:
        cta, cta_label = 'workspace', 'View receipt'
    elif state == 'SUBMISSION_UNVERIFIED':
        cta, cta_label = 'workspace', 'Verify submission'
    elif state in {'ASSESSMENT', 'INTERVIEW', 'OFFER', 'REJECTED', 'WITHDRAWN'}:
        cta = 'tracker'
        cta_label = STATE_LABELS.get(state, state.title())
    else:
        cta = 'workspace'
        cta_label = STATE_LABELS.get(
            state, state.replace('_', ' ').title()
        )

    return {
        'job_id': int(job_id),
        'application_id': application_id or None,
        'operation_id': (prepare or {}).get('operation_id'),
        'state': state,
        'authority': authority,
        'display_status': (
            'Applying now'
            if authority == 'runtime_resolution_in_progress'
            else STATE_LABELS.get(
                state,
                state.replace('_', ' ').title() if state else 'Not started',
            )
        ),
        'progress_percent': progress,
        'prepare': prepare,
        'lifecycle': lifecycle,
        'tasks': tasks,
        'runtime_questions': runtime_questions,
        'artifacts': artifacts,
        'package_review': package_review,
        'execution_mandate': execution_mandate,
        'internal_integrity_review': internal_review,
        # Retired user-facing late-review field: deliberately never populated.
        'customer_review': None,
        'production_receipt': production_receipt,
        'provider_route': provider_route,
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
        try:
            result = flow_for_job(int(job_id), summary=True)
        except Exception as projection_error:  # noqa: BLE001 - durable replay wins
            _LOGGER.warning(
                'PREPARE_ROUTE_PROJECTION_DEFERRED job_id=%s operation_id=%s application_id=%s error_type=%s',
                int(job_id),
                existing.get('operation_id'),
                existing.get('application_id'),
                type(projection_error).__name__,
            )
            result = {
                'job_id': int(job_id),
                'application_id': existing.get('application_id'),
                'operation_id': existing.get('operation_id'),
                'state': existing.get('status') or 'RUNNING',
                'display_status': 'Preparing',
                'cta': 'open_application',
                'cta_label': 'Open application',
                'submission_authority': False,
            }
        result['application_id'] = (
            result.get('application_id') or existing.get('application_id')
        )
        result['operation_id'] = (
            result.get('operation_id') or existing.get('operation_id')
        )
        result['replayed'] = True
        _LOGGER.info(
            'PREPARE_OPERATION_REPLAYED job_id=%s operation_id=%s application_id=%s',
            int(job_id),
            existing.get('operation_id'),
            existing.get('application_id'),
        )
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

    operation = prepare.start_prepare(
        application_id=application_id,
        job_id=int(job_id),
        engine=resolved_engine,
        idempotency_key=idempotency_key,
    )
    # The durable operation is the success boundary. A secondary UI projection
    # must never turn a committed Prepare click into an apparent failure.
    try:
        result = flow_for_job(int(job_id), summary=True)
    except Exception as projection_error:  # noqa: BLE001 - committed identity wins
        _LOGGER.warning(
            'PREPARE_ROUTE_PROJECTION_DEFERRED job_id=%s operation_id=%s application_id=%s error_type=%s',
            int(job_id),
            operation.get('operation_id'),
            application_id,
            type(projection_error).__name__,
        )
        result = {
            'job_id': int(job_id),
            'application_id': application_id,
            'operation_id': operation.get('operation_id'),
            'state': operation.get('status') or 'QUEUED',
            'display_status': 'Preparing',
            'cta': 'open_application',
            'cta_label': 'Open application',
            'submission_authority': False,
        }
    result['application_id'] = (
        result.get('application_id') or operation.get('application_id') or application_id
    )
    result['operation_id'] = (
        result.get('operation_id') or operation.get('operation_id')
    )
    result['replayed'] = bool(operation.get('replayed'))
    return result


def approve_and_submit_for_job(job_id: int) -> dict[str, Any]:
    """Record the sole customer package approval before browser execution.

    No submit authority is minted here because the live browser form does not
    exist yet. The autonomous ATS worker later creates an internal integrity
    snapshot and derives the one-use submit authority from this mandate.
    """
    from app import application_execution_mandate_v1 as mandate_v1

    flow = flow_for_job(int(job_id))
    if str(flow.get('state') or '') != 'READY_FOR_APPROVAL':
        raise ValueError('Application package is not ready for its one approval.')

    review = dict(flow.get('package_review') or {})
    preflight = dict((flow.get('artifacts') or {}).get('preflight') or {})
    if not review or not str(preflight.get('preflight_package_id') or ''):
        raise ValueError('The exact pre-browser package review is unavailable.')

    click_identity = (
        f"{review['preflight_package_id']}:{review['review_digest']}:"
        "approve-and-submit-pre-browser-v2"
    )
    click_key = (
        'customer-approve-submit-'
        + hashlib.sha256(click_identity.encode('utf-8')).hexdigest()[:40]
    )
    cover_letter = None
    if str(preflight.get('cover_letter_id') or ''):
        cover_letter = {
            'cover_letter_id': str(preflight['cover_letter_id']),
            'artifact_sha256': str(
                preflight.get('cover_letter_artifact_sha256') or ''
            ),
            'page_count': int(preflight.get('cover_letter_page_count') or 0),
        }

    mandate = mandate_v1.approve_package(
        preflight_package_id=str(preflight['preflight_package_id']),
        cover_letter_decision=str(
            preflight.get('cover_letter_decision') or 'UNDETERMINED'
        ),
        cover_letter=cover_letter,
        expected_review_digest=str(review['review_digest']),
        idempotency_key=click_key,
    )
    return {
        'mandate_id': str(mandate['mandate_id']),
        'status': 'APPLYING_NOW',
        'customer_approval_complete': True,
        'additional_customer_approval_required': False,
        'submit_authorization': None,
        'submission_authority': False,
    }


def _pipeline_candidate_jobs(limit: int | None) -> list[dict[str, Any]]:
    """Fetch the durable application index in one short read connection."""
    connection = get_connection()
    try:
        owner = current_owner_readonly(connection)
        candidate_ids: list[int] = []

        limit_clause = ' LIMIT ?' if limit is not None else ''
        limit_params: tuple[int, ...] = (int(limit),) if limit is not None else ()

        if _table_exists(connection, 'prepare_operations_v1'):
            candidate_ids.extend(
                int(row['job_id'])
                for row in connection.execute(
                    f'''SELECT job_id, MAX(updated_at) AS updated
                         FROM prepare_operations_v1
                        WHERE tenant_id=? AND user_id=?
                        GROUP BY job_id
                        ORDER BY datetime(updated) DESC{limit_clause}''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        *limit_params,
                    ),
                ).fetchall()
            )

        if _table_exists(connection, 'application_lifecycle_records'):
            candidate_ids.extend(
                int(row['job_id'])
                for row in connection.execute(
                    f'''SELECT job_id, MAX(updated_at) AS updated
                         FROM application_lifecycle_records
                        WHERE tenant_id=? AND user_id=?
                        GROUP BY job_id
                        ORDER BY datetime(updated) DESC{limit_clause}''',
                    (
                        owner.tenant_id,
                        owner.user_id,
                        *limit_params,
                    ),
                ).fetchall()
            )
        unique_ids = list(dict.fromkeys(candidate_ids))
        if limit is not None:
            unique_ids = unique_ids[: int(limit)]
        if not unique_ids:
            return []
        placeholders = ','.join('?' for _ in unique_ids)
        jobs = connection.execute(
            f'''SELECT id, company_name, title, location_raw, remote_type,
                       hunter_score, apply_url, job_url, source,
                       source_provenance_json, detail_extraction_json
                  FROM jobs WHERE id IN ({placeholders})''',
            tuple(unique_ids),
        ).fetchall()
        by_id = {int(row['id']): dict(row) for row in jobs}
        return [by_id[job_id] for job_id in unique_ids if job_id in by_id]
    finally:
        connection.close()


def pipeline_rows(limit: int | None = 250) -> list[dict[str, Any]]:
    '''Lifecycle-first candidate pipeline without requiring an n8n result row.'''
    result: list[dict[str, Any]] = []
    for job in _pipeline_candidate_jobs(None if limit is None else int(limit)):
        job_id = int(job['id'])
        try:
            state = flow_for_job(job_id, summary=True)
        except Exception as error:  # noqa: BLE001 - isolate one malformed application
            # One corrupt or partially migrated application must not make the
            # remaining Application Center disappear. Keep the projection
            # value-free and let the shared view model render a safe issue.
            result.append(
                {
                    **dict(job),
                    'job_id': job_id,
                    'display_status': 'Needs attention',
                    'lifecycle_state': None,
                    'prepare_status': None,
                    'progress_percent': 0,
                    'application_id': None,
                    'updated_at': None,
                    'status_evidence': 'projection_error',
                    'runtime_question_count': 0,
                    'verified_receipt_id': None,
                    'projection_error': type(error).__name__,
                    'submission_authority': False,
                }
            )
            continue
        prepare = state.get('prepare') or {}
        lifecycle = state.get('lifecycle') or {}
        result.append(
            {
                **dict(job),
                'job_id': job_id,
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
                'runtime_question_count': len(state['runtime_questions']),
                'verified_receipt_id': lifecycle.get('verified_receipt_id'),
                '_flow_projection': state,
                'submission_authority': False,
            }
        )
    return result
