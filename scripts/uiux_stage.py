"""Real MUNSHI app, synthetic DB, no production-capable execution lanes.

Executed only inside deploy/uiux/compose.yaml. --seed initializes a new tmpfs DB.
The Streamlit path runs the unchanged application entrypoint with I/O guards;
it does not mock renderers, widgets, routing, session state or data projections.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.uiux_stage_guard import DATABASE, assert_stage


def seed() -> None:
    assert_stage()
    if DATABASE.exists():
        raise RuntimeError("Refusing to replace an existing staging database; recreate only this disposable container")
    # This key exists only in this container's tmpfs, beside its disposable DB.
    # Exercise the normal AES-GCM profile authority; never borrow a live key.
    import base64
    import secrets
    key_path = Path('/app/hunter/.runtime/uiux-vault.key')
    if os.environ.get('MUNSHI_VAULT_KEY_FILE') != str(key_path) or os.environ.get('MUNSHI_VAULT_KEY'):
        raise RuntimeError('Synthetic staging requires its own ephemeral vault file')
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    from app import database
    from app import staging_fixtures
    from scripts.phase17_staging_migrate import apply as phase17
    from scripts.complete_loop_migrate import apply as complete_loop

    database.initialize_database()
    phase17()
    complete_loop(DATABASE, environment="staging", full_quick_check=True)
    conn = database.get_connection()
    try:
        staging_fixtures.seed(conn, identity="staging", database_path=DATABASE, dry_run=False)
        existing = conn.execute("SELECT id FROM jobs ORDER BY id").fetchall()
        for index in range(80):
            title = ("People Analytics Analyst", "HR Operations Coordinator", "Talent Acquisition Analyst", "Compensation and Benefits Analyst")[index % 4]
            employer = ("TEST/STAGING Northstar Research Institute", "TEST/STAGING International Education and Research Foundation", "TEST/STAGING Harbor Health Systems", "TEST/STAGING Atlas Financial Group")[index % 4]
            key = f"uiux-{index}"
            conn.execute(
                """INSERT INTO jobs(job_fingerprint,source,company_name,title,location_raw,remote_type,
                   employment_type,description_raw,hunter_score,job_url,apply_url,status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,'found')""",
                (hashlib.sha256(key.encode()).hexdigest(), "staging_fixture", employer, title,
                 ("New York, NY", "Philadelphia, PA", "Remote, US")[index % 3],
                 ("Hybrid", "On site", "Remote")[index % 3], "Full time",
                 "Synthetic staging role. Coordinate HR operations, analyze people data with Excel and Power BI, and support recruiting. No live application endpoint.",
                 98 - index % 25, f"https://staging-fixtures.invalid/{key}", f"https://staging-fixtures.invalid/{key}"),
            )
        for index, state in enumerate(("READY_FOR_REVIEW", "NEEDS_INPUT", "FAILED_SAFELY", "SUBMISSION_UNVERIFIED")):
            conn.execute(
                """INSERT INTO application_lifecycle_records(application_id,tenant_id,user_id,job_id,provider,state)
                   VALUES (?,'default','local-owner',?,'synthetic_fixture',?)""",
                (f"uiux-application-{index}", int(existing[index][0]), state),
            )
        conn.commit()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        print("UIUX_SYNTHETIC_SEED=PASS; jobs=99; production_state=absent", flush=True)
    finally:
        conn.close()


def render() -> None:
    from scripts.uiux_stage_guard import install
    install()
    import streamlit as st
    started = time.perf_counter()
    st.session_state["_uiux_full_runs"] = st.session_state.get("_uiux_full_runs", 0) + 1
    runpy.run_path(str(ROOT / "app" / "dashboard.py"), run_name="__main__")
    seconds = time.perf_counter() - started
    st.markdown(
        f'<aside id="uiux-runtime" data-runs="{st.session_state["_uiux_full_runs"]}" data-server-seconds="{seconds:.4f}" '
        'style="font-size:12px;color:#52636a;padding:1rem 0">Synthetic staging · external operations and workers disabled</aside>',
        unsafe_allow_html=True,
    )
    print(json.dumps({"uiux_full_runs": st.session_state["_uiux_full_runs"], "view": str(st.query_params.get("view", "dashboard")), "server_seconds": round(seconds, 4)}), flush=True)


if __name__ == "__main__":
    if "--seed" in sys.argv:
        seed()
    else:
        render()
