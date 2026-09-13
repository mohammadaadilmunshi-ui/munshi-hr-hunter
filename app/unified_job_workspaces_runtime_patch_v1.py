"""Runtime-safe installer for unified job workspaces.

Streamlit link buttons are identity-stable by their label/URL and do not accept a
widget key on the supported runtime.  The installer also binds Tracker to the
already-installed V2.2 humanized tracker projection before replacing the visual
surface with Dashboard-style job cards.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

from app import unified_job_workspaces_v1 as workspaces


def _card_grid(
    product_pages: Any,
    rows: list[dict[str, Any]],
    *,
    key_prefix: str,
    columns: int = 3,
    limit: int = 18,
    footer_by_id: dict[int, str] | None = None,
    application_links: bool = False,
) -> None:
    visible = rows[: max(1, int(limit))]
    if not visible:
        return
    count = min(max(1, int(columns)), len(visible))
    grid = st.columns(count, gap="medium")
    for index, row in enumerate(visible):
        job_id = int(row["id"])
        with grid[index % count]:
            product_pages._job_card(row, key_prefix=key_prefix)
            footer = str((footer_by_id or {}).get(job_id) or "").strip()
            if footer:
                st.caption(footer)
            if application_links:
                # URL is unique per job, so Streamlit gives each link a distinct
                # element identity without relying on unsupported key= syntax.
                st.link_button(
                    "Work on this application",
                    f"?view=prepare-application&job={job_id}",
                    use_container_width=True,
                )


def _pipeline_records(product_pages: Any) -> list[dict[str, Any]]:
    legacy = product_pages.tracker_rows(limit=250)
    try:
        from app import product_application_flow_v2 as complete_loop

        modern = complete_loop.pipeline_rows(limit=250)
    except Exception:
        modern = []

    merged: dict[int, dict[str, Any]] = {
        int(row["job_id"]): dict(row)
        for row in legacy
        if row.get("job_id") is not None
    }
    for row in modern:
        if row.get("job_id") is None:
            continue
        job_id = int(row["job_id"])
        combined = dict(merged.get(job_id) or {})
        combined.update(dict(row))
        merged[job_id] = combined

    rows = list(merged.values())
    rows.sort(
        key=lambda row: str(
            row.get("updated_at")
            or row.get("completed_at")
            or row.get("queued_at")
            or ""
        ),
        reverse=True,
    )
    return rows


def install_unified_job_workspaces(product_pages: Any, application_workspace_page: Any) -> None:
    workspaces._card_grid = _card_grid
    workspaces._pipeline_records = lambda: _pipeline_records(product_pages)
    workspaces.install_unified_job_workspaces(product_pages, application_workspace_page)