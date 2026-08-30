from __future__ import annotations

import pandas as pd
import streamlit as st

from app.database import get_connection
from app.market_board_discovery import (
    classify_url,
    import_existing_career_urls,
    initialize_market_tables,
)


st.set_page_config(
    page_title="Market Coverage",
    page_icon="🌐",
    layout="wide",
)

st.title("🌐 Market Coverage")
st.caption(
    "Manage public company career pages, public APIs, RSS/Atom/XML feeds, "
    "and generic Schema.org JobPosting coverage. All retrieved jobs still "
    "pass the central dashboard targeting gate before storage."
)

if st.button("Import and classify existing ATS registry career URLs"):
    result = import_existing_career_urls()
    st.success(
        f"Scanned {result['scanned']}; inserted {result['inserted']}; "
        f"updated {result['updated']}; skipped direct ATS URLs "
        f"{result['skipped_direct']}."
    )
    st.json(result)

connection = get_connection()
try:
    initialize_market_tables(connection)
    connection.commit()

    with st.form("add_market_board"):
        st.subheader("Add a public board or feed")
        col1, col2 = st.columns(2)
        with col1:
            company_name = st.text_input("Company or board name")
            board_url = st.text_input("Public careers, API, RSS, Atom, or XML URL")
        with col2:
            enabled = st.checkbox("Enabled", value=True)
            priority_weight = st.number_input(
                "Priority weight",
                min_value=0,
                max_value=100,
                value=10,
                step=1,
            )
        notes = st.text_input("Notes, optional")
        submitted = st.form_submit_button("Save board")

        if submitted:
            if not company_name.strip() or not board_url.strip():
                st.error("Company/board name and URL are required.")
            else:
                classified = classify_url(board_url)
                connection.execute(
                    """
                    INSERT INTO market_public_boards(
                        company_name,provider,source_kind,board_locator,
                        board_url,enabled,priority_weight,notes
                    ) VALUES(?,?,?,?,?,?,?,?)
                    ON CONFLICT(company_name,board_url) DO UPDATE SET
                        provider=excluded.provider,
                        source_kind=excluded.source_kind,
                        board_locator=excluded.board_locator,
                        enabled=excluded.enabled,
                        priority_weight=excluded.priority_weight,
                        notes=excluded.notes,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        company_name.strip(),
                        classified["provider"],
                        classified["source_kind"],
                        classified["board_locator"],
                        board_url.strip(),
                        int(enabled),
                        int(priority_weight),
                        notes.strip() or None,
                    ),
                )
                connection.commit()
                st.success(
                    f"Saved as {classified['provider']} / "
                    f"{classified['source_kind']}."
                )

    rows = connection.execute(
        """
        SELECT
            id,company_name,provider,source_kind,board_locator,
            board_url,enabled,priority_weight,notes,updated_at
        FROM market_public_boards
        ORDER BY enabled DESC,priority_weight DESC,company_name COLLATE NOCASE
        """
    ).fetchall()

    st.subheader("Configured public boards and feeds")
    if rows:
        frame = pd.DataFrame([dict(row) for row in rows])
        edited = st.data_editor(
            frame,
            use_container_width=True,
            hide_index=True,
            disabled=[
                "id", "provider", "source_kind",
                "board_locator", "updated_at",
            ],
            key="market_public_boards_editor",
        )
        if st.button("Save board enable/priority/notes changes"):
            for record in edited.to_dict(orient="records"):
                connection.execute(
                    """
                    UPDATE market_public_boards
                    SET
                        company_name=?,
                        board_url=?,
                        enabled=?,
                        priority_weight=?,
                        notes=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (
                        str(record["company_name"]).strip(),
                        str(record["board_url"]).strip(),
                        int(bool(record["enabled"])),
                        int(record["priority_weight"]),
                        (
                            str(record["notes"]).strip()
                            if record.get("notes") not in (None, "")
                            else None
                        ),
                        int(record["id"]),
                    ),
                )
            connection.commit()
            st.success("Market coverage board settings saved.")
    else:
        st.info(
            "No generic public boards are configured yet. "
            "Use the import button or add a careers/feed URL."
        )

    st.subheader("Coverage source status")
    source_rows = connection.execute(
        """
        SELECT
            source_name,enabled,cadence_minutes,health_status,
            raw_jobs_last_run,eligible_jobs_last_run,
            inserted_jobs_last_run,provider_used_last_run,
            last_run_at,last_error
        FROM source_health
        WHERE source_name IN (
            'Remote OK','Remotive','Jobicy','Arbeitnow',
            'Workable','Recruitee','Schema JobPosting',
            'RSS Job Feeds','USAJobs'
        )
        ORDER BY source_tier,source_name
        """
    ).fetchall()
    if source_rows:
        st.dataframe(
            pd.DataFrame([dict(row) for row in source_rows]),
            use_container_width=True,
            hide_index=True,
        )
finally:
    connection.close()
