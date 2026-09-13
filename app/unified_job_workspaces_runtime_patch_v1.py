"""Runtime-safe installer for unified job workspaces.

Streamlit link buttons are identity-stable by their label/URL and do not accept a
widget key on the supported runtime.  Patch only the shared grid helper before
installing the V1 page overrides so Dashboard-style cards can be reused safely.
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


def install_unified_job_workspaces(product_pages: Any, application_workspace_page: Any) -> None:
    workspaces._card_grid = _card_grid
    workspaces.install_unified_job_workspaces(product_pages, application_workspace_page)
