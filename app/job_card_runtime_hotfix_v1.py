"""Runtime hardening for product job-card details.

The job-card drawer is a browsing surface and must not crash merely because an
optional preparation-history table/column is absent or on an older schema. This
module keeps the card/detail UX read-only and fail-soft while preserving all
existing Save/Pass/Prepare/application authorities.
"""
from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from app.database import get_connection

VERSION = "job-card-runtime-hotfix-v1"
MARKER = "AADIL_JOB_CARD_DRAWER_FAIL_SOFT_V1"

_LOG = logging.getLogger(__name__)


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


def _latest_optional_row(
    connection: Any,
    *,
    table: str,
    job_id: int,
    wanted: tuple[str, ...],
    extra_where: str = "",
) -> dict[str, Any]:
    tables = _tables(connection)
    if table not in tables:
        return {}
    columns = _columns(connection, table)
    if "job_id" not in columns:
        return {}
    selected = [name for name in wanted if name in columns]
    if not selected:
        return {}
    order = "id DESC" if "id" in columns else "rowid DESC"
    sql = (
        f'SELECT {", ".join(selected)} FROM "{table}" '
        "WHERE job_id=? "
        + (extra_where + " " if extra_where else "")
        + f"ORDER BY {order} LIMIT 1"
    )
    row = connection.execute(sql, (int(job_id),)).fetchone()
    return dict(row) if row else {}


def _safe_job_bundle(job_id: int) -> dict[str, Any] | None:
    """Return job + optional UI history without requiring optional tables/columns."""
    connection = get_connection()
    try:
        tables = _tables(connection)
        if "jobs" not in tables:
            return None
        job = connection.execute(
            "SELECT * FROM jobs WHERE id=?",
            (int(job_id),),
        ).fetchone()
        if not job:
            return None

        state: dict[str, Any] = {}
        if "product_job_state" in tables:
            state_columns = _columns(connection, "product_job_state")
            wanted_state = [name for name in ("saved", "skipped") if name in state_columns]
            if "job_id" in state_columns and wanted_state:
                row = connection.execute(
                    f'SELECT {", ".join(wanted_state)} FROM product_job_state WHERE job_id=?',
                    (int(job_id),),
                ).fetchone()
                state = dict(row) if row else {}

        result_columns = _columns(connection, "n8n_results") if "n8n_results" in tables else set()
        result_wanted = (
            "id",
            "n8n_status",
            "final_ats_score",
            "resume_doc_url",
            "resume_pdf_url",
            "cover_letter_doc_url",
            "sent_at",
            "completed_at",
        )
        latest = _latest_optional_row(
            connection,
            table="n8n_results",
            job_id=int(job_id),
            wanted=result_wanted,
        )

        url_columns = [
            name
            for name in ("resume_pdf_url", "resume_doc_url", "cover_letter_doc_url")
            if name in result_columns
        ]
        artifact_where = ""
        if url_columns:
            artifact_where = "AND (" + " OR ".join(
                f"trim(COALESCE({name},'')) != ''" for name in url_columns
            ) + ")"
        artifact = (
            _latest_optional_row(
                connection,
                table="n8n_results",
                job_id=int(job_id),
                wanted=result_wanted,
                extra_where=artifact_where,
            )
            if artifact_where
            else {}
        )

        queue = _latest_optional_row(
            connection,
            table="n8n_dispatch_queue",
            job_id=int(job_id),
            wanted=("queue_status", "queued_at", "updated_at"),
        )
    finally:
        connection.close()

    row = dict(job)
    row["saved"] = int(state.get("saved") or 0)
    row["skipped"] = int(state.get("skipped") or 0)
    row["latest_result"] = latest
    row["artifact_result"] = artifact
    row["queue"] = queue
    return row


def _safe_open_dialog(product_v22: Any, pages: Any, job_id: int) -> None:
    try:
        product_v22._opportunity_dialog(int(job_id))
    except Exception as error:  # fail-soft browsing boundary; logged server-side
        _LOG.exception("Job detail drawer failed safely for job_id=%s", int(job_id))
        if hasattr(pages, "_set_action_feedback"):
            pages._set_action_feedback(
                "error",
                "Job details could not open safely. The job remains stored; reload or try another card.",
            )
        else:
            st.session_state["product_action_feedback"] = {
                "tone": "error",
                "message": "Job details could not open safely. The job remains stored.",
            }
        st.session_state["_job_drawer_last_error_type"] = type(error).__name__
        # The queue/query id has already been consumed, so rerun closes the empty
        # modal instead of reopening it or looping.
        st.rerun()


def _job_detail_fail_soft(product_v22: Any, pages: Any) -> None:
    state_key = getattr(
        pages,
        "_PRODUCT_JOB_DIALOG_STATE_KEY",
        "_product_job_dialog_once",
    )
    queued = st.session_state.pop(state_key, None)
    if queued is not None:
        try:
            job_id = int(queued)
        except (TypeError, ValueError):
            return
        # A local one-shot open request is already authoritative for this rerun.
        # Remove a duplicate deep-link value (for example from a completed live
        # n8n console jump) so closing the drawer cannot reopen it on the next run.
        try:
            if "job" in st.query_params:
                del st.query_params["job"]
        except Exception:
            pass
        _safe_open_dialog(product_v22, pages, job_id)
        return

    job_id = product_v22._consume_query_id("job", "_product_v22_job_once")
    if job_id is not None:
        _safe_open_dialog(product_v22, pages, int(job_id))


def install_job_card_runtime_hotfix() -> dict[str, Any]:
    from app import product_pages as pages
    from app import product_v22

    product_v22._job_bundle = _safe_job_bundle

    current = getattr(pages, "_job_detail", None)
    if not getattr(current, "_aadil_job_card_fail_soft_v1", False):
        def installed() -> None:
            _job_detail_fail_soft(product_v22, pages)

        installed._aadil_job_card_fail_soft_v1 = True  # type: ignore[attr-defined]
        installed._aadil_prior_job_detail = current  # type: ignore[attr-defined]
        pages._job_detail = installed

    return {
        "success": True,
        "version": VERSION,
        "marker": MARKER,
        "optional_n8n_history_required_for_browsing": False,
        "submission_authority": False,
    }
