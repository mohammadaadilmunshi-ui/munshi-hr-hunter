"""The disposable browser-test runtime must never acquire production authority."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_compose_has_no_production_mounts_workers_or_public_ports():
    source = (ROOT / "deploy/uiux/compose.yaml").read_text()
    for token in ('internal: true', 'read_only: true', '127.0.0.1:', 'cap_drop: [ALL]', 'security_opt: [no-new-privileges:true]', 'uiux-synthetic.db', 'HUNTER_ENABLE_TELEGRAM: "false"', 'HUNTER_ENABLE_COORDINATOR: "false"', 'PRODUCTION_CALLBACKS_ENABLED: "false"'):
        assert token in source
    assert "volumes:" not in source
    assert "/opt/munshi" not in source
    assert "env_file:" not in source
    assert "hunter-supervisor" not in source
    assert "docker.sock" not in source
    assert "MUNSHI_VAULT_KEY_FILE: /app/hunter/.runtime/uiux-vault.key" in source
    assert "MUNSHI_VAULT_KEY:" not in source
    seed = (ROOT / 'scripts/uiux_stage.py').read_text()
    assert 'secrets.token_bytes(32)' in seed
    assert 'os.O_EXCL, 0o600' in seed


def test_stage_entrypoint_refuses_normal_environment(monkeypatch):
    from scripts.uiux_stage_guard import assert_stage
    monkeypatch.delenv("MUNSHI_UIUX_STAGE", raising=False)
    import pytest
    with pytest.raises(RuntimeError, match="isolated synthetic"):
        assert_stage()


def test_stage_guards_deny_execution_and_sensitive_queue_writes():
    # A subprocess confines the deliberately permanent Python audit hook.
    source = '''
import sqlite3,socket,subprocess
from scripts import uiux_stage_guard as guard
from app import database
guard.assert_stage=lambda:None
c=sqlite3.connect(":memory:")
c.execute("CREATE TABLE product_job_state(job_id INTEGER,saved INTEGER)")
c.execute("CREATE TABLE application_submit_commands_v1(id INTEGER)")
c.execute("CREATE TABLE n8n_dispatch_queue(id INTEGER)")
database.get_connection=lambda:c
guard.install()
c=database.get_connection()
c.execute("INSERT INTO product_job_state VALUES(1,1)")
for table in ("application_submit_commands_v1","n8n_dispatch_queue"):
 try:c.execute("INSERT INTO "+table+" VALUES(1)")
 except sqlite3.DatabaseError:pass
 else:raise AssertionError("Queue write was allowed")
try:subprocess.run(["python","--version"])
except PermissionError:pass
else:raise AssertionError("Worker execution was allowed")
try:socket.socket().connect(("127.0.0.1",1))
except PermissionError:pass
else:raise AssertionError("Outbound connection was allowed")
'''
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_canonical_image_packages_streamlit_theme():
    assert "COPY .streamlit ./.streamlit" in (ROOT / "Dockerfile").read_text()
