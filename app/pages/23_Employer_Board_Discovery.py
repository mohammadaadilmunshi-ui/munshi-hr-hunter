from __future__ import annotations

import subprocess

import pandas as pd
import streamlit as st

from app.database import ROOT_DIR, get_connection


st.set_page_config(
    page_title="Employer Board Discovery",
    page_icon="🔎",
    layout="wide",
)

st.title("🔎 Employer Board Discovery")
st.caption(
    "Automatically discovers and live-validates public Personio, "
    "Pinpoint, Comeet, and Recruitee employer boards. Uses the "
    "free Common Crawl URL index plus existing Hunter URLs. "
    "No tenant guessing, private credentials, paid APIs, "
    "Telegram sends, or n8n calls."
)


def load_data():
    connection = get_connection()
    try:
        candidates = pd.read_sql_query(
            """
            SELECT
                id,provider,company_name,board_locator,
                CASE
                    WHEN public_token IS NULL OR trim(public_token)=''
                    THEN 0 ELSE 1
                END AS has_public_token,
                validation_status,last_http_status,visible_jobs,
                discovery_source,last_seen_at,last_validated_at,
                validation_error
            FROM employer_board_discovery_candidates
            ORDER BY
                CASE validation_status
                    WHEN 'valid' THEN 0
                    WHEN 'pending' THEN 1
                    WHEN 'unresolved' THEN 2
                    ELSE 3
                END,
                provider,company_name
            """,
            connection,
        )
        runs = pd.read_sql_query(
            """
            SELECT *
            FROM employer_board_discovery_runs
            ORDER BY id DESC
            LIMIT 50
            """,
            connection,
        )
        counts = pd.read_sql_query(
            """
            SELECT
                provider,validation_status,
                COUNT(*) AS candidate_count,
                SUM(visible_jobs) AS visible_jobs
            FROM employer_board_discovery_candidates
            GROUP BY provider,validation_status
            ORDER BY provider,validation_status
            """,
            connection,
        )
    finally:
        connection.close()
    return candidates, runs, counts


if st.button(
    "Run controlled public-board discovery now",
    type="primary",
    use_container_width=True,
):
    python = ROOT_DIR / ".venv/bin/python"
    command = [
        str(python),
        "-m",
        "app.employer_board_discovery_worker",
        "--run-now",
        "--max-records-per-pattern",
        "300",
        "--max-validate",
        "60",
        "--request-delay-seconds",
        "0.8",
    ]
    with st.spinner(
        "Querying the open index and validating a bounded "
        "set of public board endpoints..."
    ):
        completed = subprocess.run(
            command,
            cwd=ROOT_DIR,
            text=True,
            capture_output=True,
            timeout=45 * 60,
            check=False,
        )
    if completed.returncode == 0:
        st.success("Discovery run completed.")
    else:
        st.error("Discovery run completed with an error.")
    st.code(
        (completed.stdout + "\n" + completed.stderr)[-12000:],
        language="json",
    )
    st.rerun()

candidates, runs, counts = load_data()

valid_count = int(
    (candidates["validation_status"] == "valid").sum()
) if not candidates.empty else 0
pending_count = int(
    candidates["validation_status"].isin(["pending", "retry"]).sum()
) if not candidates.empty else 0
unresolved_count = int(
    (candidates["validation_status"] == "unresolved").sum()
) if not candidates.empty else 0
visible_jobs = int(
    candidates["visible_jobs"].fillna(0).sum()
) if not candidates.empty else 0

metrics = st.columns(4)
metrics[0].metric("Live-validated boards", valid_count)
metrics[1].metric("Pending validation", pending_count)
metrics[2].metric("Unresolved public boards", unresolved_count)
metrics[3].metric("Visible jobs at validation", visible_jobs)

st.subheader("Provider coverage")
st.dataframe(counts, use_container_width=True, hide_index=True)

st.subheader("Discovery candidates")
st.info(
    "Public Comeet tokens are never displayed here. "
    "Only whether a validated public token exists is shown."
)
st.dataframe(candidates, use_container_width=True, hide_index=True)

st.subheader("Recent discovery runs")
st.dataframe(runs, use_container_width=True, hide_index=True)
