"""Live, read-only dashboard console for explicit stored-job n8n runs.

The console is presentation-only. It reads the existing Hunter queue, universal
progress row, n8n execution correlation, callback result, and stored events. It
never dispatches, retries, submits, or mutates application authority.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

import streamlit as st

from app.database import get_connection

SUBMISSION_AUTHORITY = False
VERSION = "n8n-live-console-v1"

_ACTIVE_JOB_KEY = "n8n_live_console_job_id"
_MINIMIZED_KEY = "n8n_live_console_minimized"
_TERMINAL_KEY = "n8n_live_console_terminal"
_NOTIFIED_KEY = "n8n_live_console_notified"
_STARTED_AT_KEY = "n8n_live_console_started_at"
_START_MESSAGE_KEY = "n8n_live_console_start_message"
_LAST_SNAPSHOT_KEY = "n8n_live_console_last_snapshot"

_FAILURE_STATES = {
    "failed",
    "error",
    "crashed",
    "cancelled",
    "canceled",
}
_ACTIVE_EXECUTION_STATES = {
    "new",
    "running",
    "waiting",
    "unknown",
}

_EVENT_LABELS = {
    "stored_job_n8n_worker_started": "Worker started",
    "stored_job_n8n_queue_ready": "Queue ready",
    "stored_job_n8n_dispatched": "Webhook accepted",
    "stored_job_n8n_completed": "Hunter callback received",
    "stored_job_n8n_failed": "Run stopped",
}


def _rerun_app() -> None:
    try:
        st.rerun(scope="app")
    except TypeError:
        st.rerun()


def activate_live_run(job_id: int, start_result: Mapping[str, Any] | None = None) -> None:
    """Attach the dashboard console to one explicit Run n8n request."""
    result = dict(start_result or {})
    st.session_state[_ACTIVE_JOB_KEY] = int(job_id)
    st.session_state[_MINIMIZED_KEY] = False
    st.session_state[_TERMINAL_KEY] = False
    st.session_state[_NOTIFIED_KEY] = False
    st.session_state[_STARTED_AT_KEY] = datetime.now(timezone.utc).isoformat()
    st.session_state[_START_MESSAGE_KEY] = str(
        result.get("message") or "n8n preparation request accepted."
    )
    st.session_state.pop(_LAST_SNAPSHOT_KEY, None)


def clear_live_run() -> None:
    for key in (
        _ACTIVE_JOB_KEY,
        _MINIMIZED_KEY,
        _TERMINAL_KEY,
        _NOTIFIED_KEY,
        _STARTED_AT_KEY,
        _START_MESSAGE_KEY,
        _LAST_SNAPSHOT_KEY,
    ):
        st.session_state.pop(key, None)


def _tables(connection: Any) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(connection: Any, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _latest_row(
    connection: Any,
    *,
    table: str,
    job_id: int,
    tables: set[str],
) -> dict[str, Any]:
    if table not in tables:
        return {}
    columns = _columns(connection, table)
    if "job_id" not in columns:
        return {}
    order = "id DESC" if "id" in columns else "rowid DESC"
    row = connection.execute(
        f'SELECT * FROM "{table}" WHERE job_id=? ORDER BY {order} LIMIT 1',
        (int(job_id),),
    ).fetchone()
    return dict(row) if row else {}


def _latest_event(connection: Any, job_id: int, tables: set[str]) -> dict[str, Any]:
    if "events" not in tables:
        return {}
    columns = _columns(connection, "events")
    required = {"job_id", "event_type"}
    if not required.issubset(columns):
        return {}
    selected = [
        name
        for name in ("id", "event_type", "event_status", "payload_json", "created_at")
        if name in columns
    ]
    order = "id DESC" if "id" in columns else "rowid DESC"
    row = connection.execute(
        f"""SELECT {', '.join(selected)}
              FROM events
             WHERE job_id=?
               AND (event_type LIKE 'stored_job_n8n_%'
                    OR event_type='universal_n8n_progress_monitor')
             ORDER BY {order}
             LIMIT 1""",
        (int(job_id),),
    ).fetchone()
    return dict(row) if row else {}


def _load_records(job_id: int) -> dict[str, Any]:
    connection = get_connection()
    try:
        tables = _tables(connection)
        job: dict[str, Any] = {}
        if "jobs" in tables:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id=?",
                (int(job_id),),
            ).fetchone()
            job = dict(row) if row else {}
        queue = _latest_row(
            connection,
            table="n8n_dispatch_queue",
            job_id=job_id,
            tables=tables,
        )
        progress = _latest_row(
            connection,
            table="telegram_n8n_progress",
            job_id=job_id,
            tables=tables,
        )
        result = _latest_row(
            connection,
            table="n8n_results",
            job_id=job_id,
            tables=tables,
        )
        event = _latest_event(connection, job_id, tables)
    finally:
        connection.close()

    execution: dict[str, Any] = {}
    if queue:
        try:
            from app import universal_n8n_progress as progress_service

            correlated = progress_service._execution_for_queue(queue)
            execution = dict(correlated or {})
        except Exception:
            # n8n execution visibility is supplemental. Queue/callback evidence
            # must remain visible even if the n8n DB is temporarily unavailable.
            execution = {}

    return {
        "job": job,
        "queue": queue,
        "progress": progress,
        "result": result,
        "event": event,
        "execution": execution,
    }


def _status(value: Any) -> str:
    return str(value or "").strip().casefold()


def _result_failed(result: Mapping[str, Any]) -> bool:
    value = _status(result.get("n8n_status") or result.get("status"))
    return value in _FAILURE_STATES


def _failure_reason(
    queue: Mapping[str, Any],
    execution: Mapping[str, Any],
    progress: Mapping[str, Any],
    result: Mapping[str, Any],
) -> str:
    queue_state = _status(queue.get("queue_status"))
    execution_state = _status(
        execution.get("status")
        or ("success" if execution.get("finished") else "")
    )
    progress_state = _status(progress.get("run_status"))
    if _result_failed(result):
        return str(result.get("error_message") or "n8n returned a failure result.")
    if queue_state in _FAILURE_STATES:
        return str(queue.get("last_error") or f"Queue ended as {queue_state}.")
    if execution_state in _FAILURE_STATES:
        return f"n8n execution ended as {execution_state}."
    if progress_state in _FAILURE_STATES:
        return str(progress.get("error_message") or "The n8n progress monitor stopped.")
    return ""


def derive_stage(
    *,
    queue: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    progress: Mapping[str, Any] | None = None,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive a user-facing stage strictly from durable runtime evidence."""
    queue = dict(queue or {})
    execution = dict(execution or {})
    progress = dict(progress or {})
    result = dict(result or {})

    failure = _failure_reason(queue, execution, progress, result)
    if failure:
        return {
            "key": "failed",
            "label": "Run stopped",
            "progress_percent": 100,
            "terminal": True,
            "success": False,
            "detail": failure,
        }

    if result:
        return {
            "key": "completed",
            "label": "Package ready",
            "progress_percent": 100,
            "terminal": True,
            "success": True,
            "detail": "Hunter received the n8n callback and recorded the package.",
        }

    execution_state = _status(
        execution.get("status")
        or ("success" if execution.get("finished") else "")
    )
    queue_state = _status(queue.get("queue_status"))

    if execution_state == "success":
        return {
            "key": "callback",
            "label": "Waiting for Hunter callback",
            "progress_percent": 88,
            "terminal": False,
            "success": False,
            "detail": "n8n finished. Hunter is waiting for the final callback payload.",
        }
    if execution_state in _ACTIVE_EXECUTION_STATES:
        execution_id = execution.get("id")
        return {
            "key": "execution",
            "label": "n8n is running",
            "progress_percent": 66,
            "terminal": False,
            "success": False,
            "detail": (
                f"Execution #{execution_id} is {execution_state}."
                if execution_id
                else f"n8n execution is {execution_state}."
            ),
        }
    if queue_state in {"accepted", "dispatching", "dispatched"}:
        return {
            "key": "dispatch",
            "label": "Waiting for n8n execution",
            "progress_percent": 44,
            "terminal": False,
            "success": False,
            "detail": "The production webhook was accepted; waiting for the correlated n8n execution.",
        }
    if queue:
        return {
            "key": "queue",
            "label": "Preparing queue",
            "progress_percent": 24,
            "terminal": False,
            "success": False,
            "detail": f"Queue #{queue.get('id') or '?'} is {queue_state or 'being prepared'}.",
        }
    return {
        "key": "starting",
        "label": "Starting worker",
        "progress_percent": 8,
        "terminal": False,
        "success": False,
        "detail": str(
            st.session_state.get(_START_MESSAGE_KEY)
            or "The detached n8n worker is starting."
        ),
    }


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _event_detail(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("event_type") or "").strip()
    if not event_type:
        return ""
    label = _EVENT_LABELS.get(event_type, event_type.replace("_", " ").strip().title())
    payload = _decode_payload(event.get("payload_json"))
    if event_type == "stored_job_n8n_queue_ready" and payload.get("queue_id"):
        return f"{label} · Queue #{payload['queue_id']}"
    if event_type == "stored_job_n8n_dispatched" and payload.get("queue_id"):
        return f"{label} · Queue #{payload['queue_id']}"
    if event_type == "stored_job_n8n_completed" and payload.get("execution_id"):
        return f"{label} · Execution #{payload['execution_id']}"
    return label


def _first_time(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elapsed(started_at: Any, completed_at: Any = None) -> str:
    start = _parse_time(started_at)
    if start is None:
        return "Just started"
    end = _parse_time(completed_at) or datetime.now(timezone.utc)
    seconds = max(0, int((end - start).total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s"


def progress_snapshot(job_id: int) -> dict[str, Any]:
    records = _load_records(int(job_id))
    stage = derive_stage(
        queue=records["queue"],
        execution=records["execution"],
        progress=records["progress"],
        result=records["result"],
    )
    job = records["job"]
    queue = records["queue"]
    execution = records["execution"]
    result = records["result"]
    progress = records["progress"]
    event = records["event"]

    started_at = _first_time(
        queue.get("queued_at"),
        progress.get("created_at"),
        st.session_state.get(_STARTED_AT_KEY),
    )
    completed_at = _first_time(
        result.get("completed_at"),
        queue.get("completed_at"),
        progress.get("completed_at"),
    )

    latest_detail = stage["detail"]
    event_detail = _event_detail(event)
    if event_detail and stage["key"] not in {"completed", "failed"}:
        latest_detail = event_detail

    return {
        "version": VERSION,
        "job_id": int(job_id),
        "company": str(job.get("company_name") or "Company"),
        "title": str(job.get("title") or "Untitled role"),
        "stage": stage,
        "queue_id": queue.get("id"),
        "queue_status": str(queue.get("queue_status") or ""),
        "execution_id": execution.get("id"),
        "execution_status": str(execution.get("status") or ""),
        "result_status": str(result.get("n8n_status") or ""),
        "final_ats_score": result.get("final_ats_score"),
        "resume_ready": bool(
            result.get("resume_pdf_url")
            or result.get("resume_doc_url")
            or result.get("resume_docx_url")
        ),
        "recruiter_found": result.get("recruiter_found"),
        "latest_detail": latest_detail,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed": _elapsed(started_at, completed_at),
        "terminal": bool(stage["terminal"]),
        "success": bool(stage["success"]),
        "submission_authority": False,
    }


def _timeline_text(snapshot: Mapping[str, Any]) -> str:
    stage_key = str((snapshot.get("stage") or {}).get("key") or "starting")
    reached = {
        "starting": 0,
        "queue": 1,
        "dispatch": 2,
        "execution": 3,
        "callback": 4,
        "completed": 5,
        "failed": 5,
    }.get(stage_key, 0)
    labels = ("Start", "Queue", "Send", "n8n", "Callback", "Ready")
    parts: list[str] = []
    for index, label in enumerate(labels):
        if stage_key == "failed" and index == reached:
            marker = "!"
        elif index < reached or stage_key == "completed":
            marker = "✓"
        elif index == reached:
            marker = "●"
        else:
            marker = "○"
        parts.append(f"{marker} {label}")
    return "  →  ".join(parts)


def _inject_css() -> None:
    st.markdown(
        """
<style>
[class*="st-key-n8n_live_console_panel"]{
    position:fixed!important;
    right:18px!important;
    bottom:max(18px,env(safe-area-inset-bottom))!important;
    z-index:99990!important;
    width:min(380px,calc(100vw - 36px))!important;
    max-height:min(76vh,680px)!important;
    overflow:auto!important;
    padding:.82rem .9rem .9rem!important;
    border:1px solid rgba(206,215,208,.96)!important;
    border-radius:20px!important;
    background:rgba(250,252,250,.97)!important;
    box-shadow:0 20px 62px rgba(20,33,41,.22)!important;
    backdrop-filter:blur(18px) saturate(1.12)!important;
    -webkit-backdrop-filter:blur(18px) saturate(1.12)!important;
}
[class*="st-key-n8n_live_console_panel"] [data-testid="stVerticalBlock"]{gap:.46rem!important}
[class*="st-key-n8n_live_console_panel"] .n8n-live-kicker{
    font-size:.66rem;font-weight:800;letter-spacing:.09em;text-transform:uppercase;color:#66737B;
}
[class*="st-key-n8n_live_console_panel"] .n8n-live-title{
    font-size:1rem;font-weight:780;line-height:1.25;color:#142129;margin-top:.08rem;
}
[class*="st-key-n8n_live_console_panel"] .n8n-live-stage{
    border:1px solid #DFE5E0;border-radius:13px;background:#FFF;padding:.62rem .7rem;
}
[class*="st-key-n8n_live_console_panel"] .n8n-live-stage b{display:block;color:#123D31}
[class*="st-key-n8n_live_console_panel"] .n8n-live-stage span{display:block;color:#66737B;font-size:.76rem;margin-top:.14rem;line-height:1.35}
[class*="st-key-n8n_live_console_panel"] .n8n-live-timeline{
    font-size:.66rem;line-height:1.45;color:#66737B;overflow-wrap:anywhere;
}
[class*="st-key-n8n_live_console_panel"] .stButton>button{min-height:34px!important}
@media (max-width:720px){
    [class*="st-key-n8n_live_console_panel"]{
        right:12px!important;
        bottom:max(12px,env(safe-area-inset-bottom))!important;
        width:calc(100vw - 24px)!important;
        max-height:58vh!important;
        border-radius:18px!important;
    }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _open_job_card(job_id: int) -> None:
    # Seed both supported routes so the jump remains reliable across wrappers.
    try:
        from app import product_pages

        state_key = getattr(
            product_pages,
            "_PRODUCT_JOB_DIALOG_STATE_KEY",
            "_product_job_dialog_once",
        )
        st.session_state[state_key] = int(job_id)
    except Exception:
        st.session_state["_product_job_dialog_once"] = int(job_id)

    st.session_state["product_view"] = "jobs"
    try:
        st.query_params["view"] = "jobs"
        st.query_params["job"] = str(int(job_id))
        for parameter in ("pipeline_job", "tab", "section"):
            if parameter in st.query_params:
                del st.query_params[parameter]
    except Exception:
        pass
    clear_live_run()
    _rerun_app()


def _render_panel(snapshot: Mapping[str, Any]) -> None:
    _inject_css()
    job_id = int(snapshot["job_id"])
    stage = dict(snapshot.get("stage") or {})
    minimized = bool(st.session_state.get(_MINIMIZED_KEY, False))

    with st.container(key="n8n_live_console_panel"):
        if minimized:
            left, right = st.columns((5, 1), gap="small")
            with left:
                st.markdown(
                    f'<div class="n8n-live-kicker">n8n live · {html.escape(str(snapshot.get("elapsed") or ""))}</div>'
                    f'<div class="n8n-live-title">{html.escape(str(stage.get("label") or "Running"))}</div>',
                    unsafe_allow_html=True,
                )
            with right:
                if st.button("▴", key=f"n8n_live_expand_{job_id}", help="Expand live console"):
                    st.session_state[_MINIMIZED_KEY] = False
                    _rerun_app()
            return

        header_left, header_right = st.columns((5, 1), gap="small")
        with header_left:
            st.markdown(
                '<div class="n8n-live-kicker">● Live n8n preparation</div>'
                f'<div class="n8n-live-title">{html.escape(str(snapshot.get("company") or "Company"))} · '
                f'{html.escape(str(snapshot.get("title") or "Role"))}</div>',
                unsafe_allow_html=True,
            )
        with header_right:
            if st.button("—", key=f"n8n_live_minimize_{job_id}", help="Minimize live console"):
                st.session_state[_MINIMIZED_KEY] = True
                _rerun_app()

        st.progress(int(stage.get("progress_percent") or 0))
        st.markdown(
            f'<div class="n8n-live-stage"><b>{html.escape(str(stage.get("label") or "Running"))}</b>'
            f'<span>{html.escape(str(snapshot.get("latest_detail") or "Watching durable n8n evidence…"))}</span></div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="n8n-live-timeline">{html.escape(_timeline_text(snapshot))}</div>',
            unsafe_allow_html=True,
        )

        runtime_bits = [f"Elapsed {snapshot.get('elapsed') or '—'}"]
        if snapshot.get("queue_id"):
            runtime_bits.append(f"Queue #{snapshot['queue_id']}")
        if snapshot.get("execution_id"):
            runtime_bits.append(f"Execution #{snapshot['execution_id']}")
        st.caption(" · ".join(runtime_bits))

        if snapshot.get("terminal"):
            if snapshot.get("success"):
                facts: list[str] = []
                if snapshot.get("final_ats_score") not in (None, ""):
                    facts.append(f"ATS {snapshot['final_ats_score']}")
                if snapshot.get("resume_ready"):
                    facts.append("Resume ready")
                if snapshot.get("recruiter_found") not in (None, ""):
                    facts.append(
                        "Recruiter found"
                        if bool(snapshot.get("recruiter_found"))
                        else "No recruiter recorded"
                    )
                st.success("Package recorded" + (" · " + " · ".join(facts) if facts else ""))
                if st.button(
                    "Open job card",
                    type="primary",
                    use_container_width=True,
                    key=f"n8n_live_open_job_{job_id}",
                ):
                    _open_job_card(job_id)
            else:
                st.error(str(stage.get("detail") or "The n8n run stopped."))
                if st.button(
                    "Open job card",
                    use_container_width=True,
                    key=f"n8n_live_open_failed_job_{job_id}",
                ):
                    _open_job_card(job_id)

            if st.button(
                "Dismiss",
                use_container_width=True,
                key=f"n8n_live_dismiss_{job_id}",
            ):
                clear_live_run()
                _rerun_app()


def _safe_snapshot(job_id: int) -> dict[str, Any]:
    try:
        snapshot = progress_snapshot(int(job_id))
    except Exception as error:  # browsing/presentation must remain fail-soft
        snapshot = {
            "version": VERSION,
            "job_id": int(job_id),
            "company": "MUNSHI",
            "title": "n8n preparation",
            "stage": {
                "key": "starting",
                "label": "Live status reconnecting",
                "progress_percent": 8,
                "terminal": False,
                "success": False,
                "detail": "Live evidence is temporarily unavailable; the n8n run is not being retried or changed.",
            },
            "latest_detail": "Live evidence is temporarily unavailable.",
            "elapsed": _elapsed(st.session_state.get(_STARTED_AT_KEY)),
            "terminal": False,
            "success": False,
            "submission_authority": False,
            "read_error_type": type(error).__name__,
        }
    st.session_state[_LAST_SNAPSHOT_KEY] = snapshot
    return snapshot


@st.fragment(run_every="2s")
def _live_fragment(job_id: int) -> None:
    snapshot = _safe_snapshot(int(job_id))
    if snapshot.get("terminal"):
        st.session_state[_TERMINAL_KEY] = True
        if not st.session_state.get(_NOTIFIED_KEY, False):
            if snapshot.get("success"):
                st.toast(
                    "n8n preparation finished. Open the job card from the live console.",
                    icon="✅",
                )
            else:
                st.toast("n8n preparation stopped. Review the live console.", icon="⚠️")
            st.session_state[_NOTIFIED_KEY] = True
        # Switch from the timed fragment to a static terminal panel so completed
        # runs do not keep polling indefinitely.
        _rerun_app()
        return
    _render_panel(snapshot)


def render_n8n_live_console() -> None:
    raw = st.session_state.get(_ACTIVE_JOB_KEY)
    if raw is None:
        return
    try:
        job_id = int(raw)
    except (TypeError, ValueError):
        clear_live_run()
        return

    if st.session_state.get(_TERMINAL_KEY, False):
        snapshot = _safe_snapshot(job_id)
        _render_panel(snapshot)
        return
    _live_fragment(job_id)
