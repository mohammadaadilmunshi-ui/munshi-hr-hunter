from __future__ import annotations

from pathlib import Path

from app import n8n_live_console_v1 as console

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_live_console_stage_progression_uses_runtime_evidence() -> None:
    queued = console.derive_stage(queue={"id": 9, "queue_status": "pending"})
    assert queued["key"] == "queue"
    assert queued["terminal"] is False

    dispatched = console.derive_stage(
        queue={"id": 9, "queue_status": "accepted"},
    )
    assert dispatched["key"] == "dispatch"

    running = console.derive_stage(
        queue={"id": 9, "queue_status": "accepted"},
        execution={"id": 41, "status": "running"},
    )
    assert running["key"] == "execution"
    assert "#41" in running["detail"]

    callback = console.derive_stage(
        queue={"id": 9, "queue_status": "accepted"},
        execution={"id": 41, "status": "success"},
    )
    assert callback["key"] == "callback"
    assert callback["terminal"] is False

    completed = console.derive_stage(
        queue={"id": 9, "queue_status": "completed"},
        execution={"id": 41, "status": "success"},
        result={"id": 88, "n8n_status": "application_ready"},
    )
    assert completed["key"] == "completed"
    assert completed["terminal"] is True
    assert completed["success"] is True
    assert completed["progress_percent"] == 100


def test_live_console_failure_is_terminal_and_never_retries() -> None:
    failed = console.derive_stage(
        queue={"id": 9, "queue_status": "failed", "last_error": "synthetic failure"},
    )
    assert failed["key"] == "failed"
    assert failed["terminal"] is True
    assert failed["success"] is False
    assert "synthetic failure" in failed["detail"]
    assert console.SUBMISSION_AUTHORITY is False

    source = _source("app/n8n_live_console_v1.py")
    assert "dispatch_pending(" not in source
    assert "start_stored_job_run(" not in source
    assert "submit_application" not in source
    assert "automatic retry" not in source.casefold()


def test_run_n8n_activates_console_and_returns_to_full_shell() -> None:
    selector = _source("app/resume_engine_selector.py")
    assert "from app.n8n_live_console_v1 import activate_live_run" in selector
    assert "activate_live_run(job_id, result)" in selector
    assert "_rerun_app()" in selector

    shell = _source("app/product_shell.py")
    assert "from app.n8n_live_console_v1 import render_n8n_live_console" in shell
    assert "render_n8n_live_console()" in shell


def test_live_console_is_fixed_polling_minimizable_and_job_card_aware() -> None:
    source = _source("app/n8n_live_console_v1.py")
    assert '@st.fragment(run_every="2s")' in source
    assert 'position:fixed!important' in source
    assert "n8n_live_console_minimized" in source
    assert '"Open job card"' in source
    assert "_PRODUCT_JOB_DIALOG_STATE_KEY" in source
    assert 'st.session_state["product_view"] = "jobs"' in source
    assert "clear_live_run()" in source


def test_terminal_console_switches_from_polling_to_static_panel() -> None:
    source = _source("app/n8n_live_console_v1.py")
    assert "st.session_state[_TERMINAL_KEY] = True" in source
    assert "Switch from the timed fragment to a static terminal panel" in source
    assert "if st.session_state.get(_TERMINAL_KEY, False):" in source
    assert "_render_panel(snapshot)" in source
