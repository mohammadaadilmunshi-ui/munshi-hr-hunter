"""Static safety contract for Netcup page-checkpoint learning service."""
from __future__ import annotations

import subprocess
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "deploy" / "netcup"
    / "munshi-relay-learning-readonly.sh"
)


def test_learning_runner_bash_syntax_and_stable_one_page_checkpoint():
    assert subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, check=False,
    ).returncode == 0
    source = SCRIPT.read_text()
    assert "--pages 1 --start-page" in source
    assert "history_next_page" in source
    assert 'validate_response "$page" save' in source
    assert 'if ! output=' in source
    assert "NO_CURSOR_ADVANCE=YES" in source
    assert "os.replace(tmp,path)" in source
    assert "flock -n 9" in source
    assert "MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED=true" in source
    assert "--submit" not in source
    assert "gh workflow run" not in source


def test_canonical_cursor_advances_only_from_verified_page():
    source = SCRIPT.read_text()
    assert 'report.get("status")!="COMPLETED_READ_ONLY"' in source
    assert 'report.get("provider_submissions_started")!=0' in source
    assert 'report.get("native_auto_upgrades_applied")!=0' in source
    assert 'report.get("history_start_page")!=page' in source
    assert "LEARNING_BLOCKED=UNVERIFIED_PAGE" in source
    assert "LEARNING_BLOCKED=MAX_HISTORY_PAGE" in source
    assert "LEARNING_BLOCKED=NONCONTIGUOUS_CURSOR" in source
    assert "RECENT_PAGE_REFRESHED=YES" in source
