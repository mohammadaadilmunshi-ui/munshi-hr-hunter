from __future__ import annotations

import sqlite3
from typing import Any

import pandas as pd
import streamlit as st

from app.database import get_connection, get_setting, save_setting
from app.dashboard_adapter_sources_v2_3_1 import SOURCE_SPECS, sync_public_boards_from_registry

st.set_page_config(page_title="Adapter Expansion", page_icon="🧭", layout="wide")
st.title("🧭 All-Adapters Activation V2.4")
st.caption("Source switches, cadence, company boards, health, and timers remain controlled by hunter.db. Personal targeting rules are not stored in adapter code.")

SOURCE_NAMES = [spec.display_name for spec in SOURCE_SPECS.values()]
BOARD_SOURCE_NAMES = ["Personio", "Pinpoint", "Comeet"]


def load_sources() -> pd.DataFrame:
    connection = get_connection()
    try:
        frame = pd.read_sql_query(
            f"""
            SELECT source_name, source_tier, enabled, cadence_minutes, cost_mode,
                   health_status, last_run_at, jobs_found_last_run, last_error
            FROM source_health
            WHERE source_name IN ({','.join('?' for _ in SOURCE_NAMES)})
            ORDER BY source_tier, source_name
            """,
            connection,
            params=SOURCE_NAMES,
        )
    finally:
        connection.close()
    if not frame.empty:
        frame["enabled"] = frame["enabled"].astype(bool)
    return frame


def save_sources(frame: pd.DataFrame) -> tuple[bool, str]:
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for _, row in frame.iterrows():
            source_name = str(row.get("source_name") or "").strip()
            if source_name not in SOURCE_NAMES:
                continue
            cadence = max(60, min(int(row.get("cadence_minutes") or 180), 1440))
            connection.execute(
                """
                UPDATE source_health
                SET enabled=?, cadence_minutes=?, updated_at=CURRENT_TIMESTAMP
                WHERE source_name=?
                """,
                (1 if bool(row.get("enabled")) else 0, cadence, source_name),
            )
        connection.commit()
        return True, "Adapter source controls saved."
    except Exception as exc:
        connection.rollback()
        return False, str(exc)
    finally:
        connection.close()


def load_boards() -> pd.DataFrame:
    connection = get_connection()
    try:
        frame = pd.read_sql_query(
            """
            SELECT id, source_name, company_name, board_locator, board_url,
                   public_token, company_uid, enabled, notes, updated_at
            FROM public_adapter_boards
            ORDER BY source_name, company_name, id
            """,
            connection,
        )
    finally:
        connection.close()
    if frame.empty:
        frame = pd.DataFrame(columns=[
            "id", "source_name", "company_name", "board_locator", "board_url",
            "public_token", "company_uid", "enabled", "notes", "updated_at", "delete",
        ])
    else:
        frame["enabled"] = frame["enabled"].astype(bool)
        frame["delete"] = False
    return frame


def save_boards(frame: pd.DataFrame) -> tuple[bool, str]:
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for _, row in frame.iterrows():
            row_id = row.get("id")
            try:
                row_id_int = int(row_id) if pd.notna(row_id) and str(row_id).strip() else None
            except (TypeError, ValueError):
                row_id_int = None
            if bool(row.get("delete")) and row_id_int:
                connection.execute("DELETE FROM public_adapter_boards WHERE id=?", (row_id_int,))
                continue
            source_name = str(row.get("source_name") or "").strip()
            company_name = str(row.get("company_name") or "").strip()
            if not source_name and not company_name:
                continue
            if source_name not in BOARD_SOURCE_NAMES:
                raise ValueError(f"Invalid board source: {source_name}")
            if not company_name:
                raise ValueError("Company name is required for each board row.")
            payload = (
                source_name,
                company_name,
                str(row.get("board_locator") or "").strip() or None,
                str(row.get("board_url") or "").strip() or None,
                str(row.get("public_token") or "").strip() or None,
                str(row.get("company_uid") or "").strip() or None,
                1 if bool(row.get("enabled")) else 0,
                str(row.get("notes") or "").strip() or None,
            )
            if row_id_int:
                connection.execute(
                    """
                    UPDATE public_adapter_boards
                    SET source_name=?, company_name=?, board_locator=?, board_url=?,
                        public_token=?, company_uid=?, enabled=?, notes=?,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    payload + (row_id_int,),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO public_adapter_boards(
                        source_name,company_name,board_locator,board_url,
                        public_token,company_uid,enabled,notes
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    payload,
                )
        connection.commit()
        return True, "Public adapter boards saved."
    except Exception as exc:
        connection.rollback()
        return False, str(exc)
    finally:
        connection.close()


def load_timers() -> pd.DataFrame:
    connection = get_connection()
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_random_schedule'").fetchone()
        if not exists:
            return pd.DataFrame()
        columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(source_random_schedule)").fetchall()]
        selected = [name for name in (
            "source_name", "schedule_state", "next_run_at", "base_cadence_minutes",
            "jitter_minutes", "schedule_reason", "last_worker_status",
            "consecutive_scheduler_failures", "updated_at",
        ) if name in columns]
        return pd.read_sql_query(
            f"SELECT {', '.join(selected)} FROM source_random_schedule WHERE source_name IN ({','.join('?' for _ in SOURCE_NAMES)}) ORDER BY source_name",
            connection,
            params=SOURCE_NAMES,
        )
    finally:
        connection.close()


source_tab, boards_tab, timers_tab, rules_tab = st.tabs(["Source controls", "Company boards", "Timers & health", "Rule ownership"])

with source_tab:
    st.subheader("Adapter switches and cadence")
    st.info("These rows are the same source_health controls used by the main Sources dashboard and Telegram /run.")
    sources = load_sources()
    edited_sources = st.data_editor(
        sources,
        use_container_width=True,
        hide_index=True,
        disabled=["source_name", "source_tier", "cost_mode", "health_status", "last_run_at", "jobs_found_last_run", "last_error"],
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "cadence_minutes": st.column_config.NumberColumn("Cadence minutes", min_value=60, max_value=1440, step=30),
        },
        key="adapter_v24_sources",
    )
    if st.button("Save adapter source controls", type="primary"):
        ok, message = save_sources(edited_sources)
        (st.success if ok else st.error)(message)

with boards_tab:
    st.subheader("Personio, Pinpoint, and Comeet public career boards")
    st.caption("Use only public careers URLs/slugs and Comeet Careers API tokens. Never paste private ATS, admin, or candidate API credentials.")
    if st.button("Import matching public boards from existing ATS registry"):
        result = sync_public_boards_from_registry()
        st.success(
            f"Registry scan complete: {result.get('inserted', 0)} new board(s) imported. "
            f"Detected: {result.get('detected', {})}"
        )
        st.rerun()
    boards = load_boards()
    edited_boards = st.data_editor(
        boards,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        disabled=["id", "updated_at"],
        column_config={
            "source_name": st.column_config.SelectboxColumn("Source", options=BOARD_SOURCE_NAMES, required=True),
            "company_name": st.column_config.TextColumn("Company", required=True),
            "board_locator": st.column_config.TextColumn("Slug / locator", help="Personio or Pinpoint company slug; for Comeet this can be the company UID."),
            "board_url": st.column_config.TextColumn("Full public endpoint (optional)"),
            "public_token": st.column_config.TextColumn("Comeet public Careers token"),
            "company_uid": st.column_config.TextColumn("Comeet company UID"),
            "enabled": st.column_config.CheckboxColumn("Enabled"),
            "delete": st.column_config.CheckboxColumn("Delete"),
        },
        key="adapter_v24_boards",
    )
    if st.button("Save public adapter boards", type="primary"):
        ok, message = save_boards(edited_boards)
        (st.success if ok else st.error)(message)

with timers_tab:
    st.subheader("Random timer and source-health state")
    st.dataframe(load_timers(), use_container_width=True, hide_index=True)
    st.dataframe(load_sources(), use_container_width=True, hide_index=True)

with rules_tab:
    st.subheader("Single source of truth")
    st.code(
        """Dashboard / hunter.db
  settings.targeting
  settings.authorization
  settings.scoring
  location_rules
        ↓
existing canonical filter_dashboard_jobs()
        ↓
job_store.save_job() scoring, CPT/auth, salary, dedupe
        ↓
Telegram cards and approval gate
        ↓
n8n only through the existing dispatcher""",
        language="text",
    )
    st.success("No target locations, job titles, CPT dates, salary rules, work-authorization rules, score thresholds, or n8n limits are hardcoded in the new adapter files.")

    st.subheader("V2.4 activation controls")
    activation = get_setting("adapter_activation", {})
    excluded_terms_text = st.text_area(
        "Excluded senior title terms (title-only guard)",
        value="\n".join(activation.get("excluded_title_terms", [])),
        height=220,
        help="Dashboard-controlled title-only exclusions applied before the canonical role matcher.",
    )
    nyc_terms_text = st.text_area(
        "NYC Open Data discovery terms",
        value="\n".join(activation.get("nyc_search_terms", [])),
        height=220,
        help="Used only to narrow the NYC source request. Final eligibility still uses the canonical dashboard rules.",
    )
    if st.button("Save V2.4 activation controls", type="primary"):
        activation.update({
            "excluded_title_terms": [line.strip() for line in excluded_terms_text.splitlines() if line.strip()],
            "nyc_search_terms": [line.strip() for line in nyc_terms_text.splitlines() if line.strip()],
        })
        save_setting("adapter_activation", activation)
        st.success("V2.4 activation controls saved to hunter.db.")
        st.rerun()
