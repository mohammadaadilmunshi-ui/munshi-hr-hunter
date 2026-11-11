"""Career OS production-safe UX corrections.

Additive runtime layer for three user-facing behaviors:
- deterministic Master Resume profile parsing with no model/API dependency;
- a selectable canonical job-extraction time-window counter;
- progressive, automatically loaded Browse Jobs scrolling instead of pages.

The layer intentionally does not change discovery workers, targeting, n8n,
submission authority, databases, adapters, or deployment/runtime contracts.
"""
from __future__ import annotations

import json
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from app.database import get_connection
from app.deterministic_profile_extractor_v1 import extract_profile_from_source


_BATCH_SIZE = 48
_WINDOW_OPTIONS = {
    "Past 1 hour": 1,
    "Past 6 hours": 6,
    "Past 12 hours": 12,
    "Past 24 hours": 24,
    "Past 3 days": 72,
    "Past 7 days": 168,
    "Past 30 days": 720,
    "All time": 0,
}


def jobs_extracted_count(hours: int) -> int:
    """Count canonical deduplicated jobs first seen within a UTC-relative window."""
    window = max(0, int(hours or 0))
    connection = get_connection()
    try:
        if window == 0:
            row = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
        else:
            row = connection.execute(
                """SELECT COUNT(*)
                     FROM jobs
                    WHERE trim(COALESCE(first_seen_at,'')) != ''
                      AND datetime(first_seen_at) >= datetime('now', ?)""",
                (f"-{window} hours",),
            ).fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def _window_help(hours: int) -> str:
    if not hours:
        return "Canonical deduplicated jobs currently stored in MUNSHI."
    if hours == 1:
        return "Canonical jobs first seen during the past hour. Provider duplicates are not inflated."
    if hours < 24:
        return f"Canonical jobs first seen during the past {hours} hours. Provider duplicates are not inflated."
    days = hours // 24
    return f"Canonical jobs first seen during the past {days} day{'s' if days != 1 else ''}. Provider duplicates are not inflated."


def _install_advanced_counter(pages_module: Any) -> None:
    from app import product_v22

    if getattr(product_v22, "_career_os_counter_installed", False):
        return

    original = product_v22.advanced_v22
    product_v22._career_os_original_advanced_v22 = original

    def advanced_with_window_counter() -> None:
        st.markdown("### Custom extraction window")
        selector, spacer = st.columns((1.1, 2.9), gap="medium")
        with selector:
            selected = st.selectbox(
                "Extraction window",
                list(_WINDOW_OPTIONS),
                index=list(_WINDOW_OPTIONS).index("Past 24 hours"),
                key="product_extraction_counter_window_v1",
            )
        with spacer:
            st.caption(
                "Choose the period you want to inspect. This counter uses the canonical jobs table and first-seen evidence, so repeated provider scans do not inflate the number."
            )
        hours = _WINDOW_OPTIONS[selected]
        value = jobs_extracted_count(hours)
        card_columns = st.columns(3, gap="medium")
        with card_columns[0]:
            product_v22._stat_card(
                "Jobs extracted",
                f"{value:,}",
                _window_help(hours),
            )
        original()

    product_v22.advanced_v22 = advanced_with_window_counter
    pages_module._advanced = advanced_with_window_counter
    product_v22._career_os_counter_installed = True


def _filter_signature(filters: dict[str, Any]) -> str:
    normalized = {}
    for key, value in filters.items():
        if isinstance(value, tuple):
            normalized[key] = list(value)
        else:
            normalized[key] = value
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)


def _increase_job_batches() -> None:
    st.session_state["product_jobs_visible_batches_v1"] = min(
        int(st.session_state.get("product_jobs_visible_batches_v1", 1)) + 1,
        60,
    )


def _autoload_sentinel() -> None:
    """Click the ordinary Streamlit load-more control when the bottom nears view."""
    st.markdown(
        '<div id="munshi-jobs-infinite-sentinel" style="height:1px;width:100%"></div>',
        unsafe_allow_html=True,
    )
    with st.container(key="product_jobs_infinite_control"):
        st.button(
            "Load more jobs",
            key="product_jobs_infinite_load_more",
            use_container_width=True,
            on_click=_increase_job_batches,
        )
        st.caption("More jobs load automatically as you approach the bottom. The button is a fallback if browser auto-load is unavailable.")

    components.html(
        """
<script>
(() => {
  try {
    const doc = window.parent.document;
    const sentinel = doc.getElementById('munshi-jobs-infinite-sentinel');
    const button = doc.querySelector('.st-key-product_jobs_infinite_control button');
    if (!sentinel || !button) return;
    let fired = false;
    const observer = new window.parent.IntersectionObserver((entries) => {
      if (!fired && entries.some((entry) => entry.isIntersecting)) {
        fired = true;
        observer.disconnect();
        button.click();
      }
    }, { root: null, rootMargin: '900px 0px 900px 0px', threshold: 0 });
    observer.observe(sentinel);
    window.setTimeout(() => observer.disconnect(), 60000);
  } catch (_) {
    /* The ordinary Load more jobs button remains a safe fallback. */
  }
})();
</script>
        """,
        height=0,
        scrolling=False,
    )


def _install_infinite_jobs(pages_module: Any) -> None:
    if getattr(pages_module, "_career_os_infinite_jobs_installed", False):
        return

    pages_module._career_os_original_browse_jobs = pages_module.browse_jobs

    def browse_jobs_infinite() -> None:
        pages_module._show_action_feedback()
        pages_module.page_intro(
            "DISCOVER",
            "Browse jobs",
            "Search every stored opportunity without losing the targeting, authorization, source, or score evidence behind the match.",
        )
        filters = pages_module._search_filters("jobs")
        signature = _filter_signature(filters)
        if st.session_state.get("product_jobs_filter_signature_v1") != signature:
            st.session_state["product_jobs_filter_signature_v1"] = signature
            st.session_state["product_jobs_visible_batches_v1"] = 1

        batches = max(1, int(st.session_state.get("product_jobs_visible_batches_v1", 1)))
        jobs: list[dict[str, Any]] = []
        count = 0
        for page in range(1, batches + 1):
            chunk, total = pages_module.fetch_jobs(
                **filters,
                page=page,
                page_size=_BATCH_SIZE,
            )
            count = int(total)
            jobs.extend(chunk)
            if len(chunk) < _BATCH_SIZE:
                break

        deduplicated: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in jobs:
            job_id = int(row["id"])
            if job_id in seen:
                continue
            seen.add(job_id)
            deduplicated.append(row)
        jobs = deduplicated

        header, add = st.columns((3.2, 1))
        with header:
            st.markdown(
                f"**{count:,} matching opportunities**  ·  "
                f"Showing {min(len(jobs), count):,} as you scroll"
            )
        with add:
            if st.button("+ Add your own", key="product_add_own", use_container_width=True):
                st.session_state["show_manual_job"] = True

        if st.session_state.get("show_manual_job"):
            pages_module._manual_add()

        if jobs:
            with st.container(key="product_job_grid"):
                for start in range(0, len(jobs), 4):
                    with st.container(key=f"product_job_grid_row_{start}"):
                        cols = st.columns(4, gap="medium")
                        for index, row in enumerate(jobs[start:start + 4]):
                            with cols[index]:
                                pages_module._job_card(row, key_prefix="jobs")
            if len(jobs) < count:
                _autoload_sentinel()
            else:
                st.caption(f"All {count:,} matching jobs are loaded.")
        else:
            st.markdown(
                '<div class="empty-product">No stored jobs match this view.</div>',
                unsafe_allow_html=True,
            )
        pages_module._job_detail()

    pages_module.browse_jobs = browse_jobs_infinite
    pages_module._career_os_infinite_jobs_installed = True


def _install_deterministic_profile() -> None:
    from app import native_resume_service_v3 as v3

    if getattr(v3, "_deterministic_profile_extractor_installed", False):
        return
    v3._openai_profile_extractor_v3 = v3.extract_profile_from_source
    v3.extract_profile_from_source = extract_profile_from_source
    v3._deterministic_profile_extractor_installed = True


def install_career_os_quality_patch(pages_module: Any) -> None:
    """Install additive product fixes after V2.2 and the Prepare chooser."""
    if getattr(pages_module, "_career_os_quality_patch_v1_installed", False):
        return
    _install_deterministic_profile()
    _install_advanced_counter(pages_module)
    _install_infinite_jobs(pages_module)
    pages_module._career_os_quality_patch_v1_installed = True
