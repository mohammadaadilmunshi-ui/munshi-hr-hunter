from __future__ import annotations

import pandas as pd
import streamlit as st

from app.source_runtime_state_v1 import (
    STATE_DISPLAY,
    STATE_ORDER,
    dataframe_rows,
    freshness_attention,
    grouped_snapshot,
)


st.set_page_config(
    page_title="Source Runtime State",
    page_icon="🧭",
    layout="wide",
)

st.title("🧭 Source Runtime State")
st.caption(
    "This page separates scheduler readiness, active execution, "
    "successful completion, retry backoff, setup requirements, "
    "and genuine first-run status."
)

groups = grouped_snapshot()
freshness = freshness_attention()

top = st.columns(4)
top[0].metric(
    "Due, waiting",
    len(
        groups[
            "due_waiting_selection"
        ]
    ),
)
top[1].metric(
    "Executing now",
    len(groups["running"]),
)
top[2].metric(
    "Completed successfully",
    len(
        groups[
            "completed_successfully"
        ]
    ),
)
top[3].metric(
    "Failed, waiting retry",
    len(
        groups[
            "failed_waiting_retry"
        ]
    ),
)

if freshness["overdue_count"]:
    st.warning(
        f"{freshness['overdue_count']} enabled source(s) are "
        "overdue beyond their cadence-aware freshness windows: "
        + ", ".join(
            freshness[
                "overdue_sources"
            ]
        )
    )
else:
    st.success(
        "Source freshness is healthy under the cadence-aware policy."
    )

st.info(
    "Freshness is not a fixed three-hour rule. The window is "
    "max(3 hours, source cadence + jitter + grace). A source in "
    "failure backoff is shown as a runtime failure, not mislabeled "
    "as a freshness failure."
)

table = pd.DataFrame(
    dataframe_rows()
)
if table.empty:
    st.warning(
        "No source-health rows were found."
    )
else:
    st.dataframe(
        table,
        hide_index=True,
        use_container_width=True,
        height=640,
    )

st.subheader("State legend")
for key in STATE_ORDER:
    emoji, label = STATE_DISPLAY[
        key
    ]
    if key == "due_waiting_selection":
        detail = (
            "The run is due and queued for randomized scheduler selection."
        )
    elif key == "running":
        detail = (
            "The worker has started and has not yet recorded completion."
        )
    elif key == "completed_successfully":
        detail = (
            "The latest worker outcome succeeded; it is waiting for its next cadence."
        )
    elif key == "failed_waiting_retry":
        detail = (
            "The latest worker outcome failed and a retry/backoff time exists."
        )
    elif key == "awaiting_first_live_run":
        detail = (
            "No run timestamp, worker outcome, or raw-count evidence exists."
        )
    elif key == "setup_required":
        detail = (
            "The adapter requires configuration or credentials."
        )
    else:
        detail = (
            "The source is disabled."
        )
    st.markdown(
        f"**{emoji} {label}:** {detail}"
    )
