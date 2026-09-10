from __future__ import annotations

import sqlite3
from pathlib import Path

from app import n8n_dispatch


def test_dispatch_transaction_commits_setup_before_begin_immediate():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample(value) VALUES (1)")
        assert connection.in_transaction is True
        n8n_dispatch._begin_dispatch_transaction(connection)
        assert connection.in_transaction is True
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 1
    finally:
        connection.close()


def test_telegram_system_status_defines_both_runtime_health_variables():
    source = Path("app/telegram_control_center.py").read_text(encoding="utf-8")
    body = source[source.index("def system_status_text()"):]
    assert 'fastapi_host, fastapi_port = service_endpoint("fastapi")' in body
    assert 'n8n_host, n8n_port = service_endpoint("n8n")' in body
    assert "fastapi_online = _port_open(fastapi_port, fastapi_host)" in body
    assert "n8n_online = _port_open(n8n_port, n8n_host)" in body


def test_prepare_runtime_is_feature_gated_and_submitless():
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    supervisor = Path("docker/hunter-supervisor.py").read_text(encoding="utf-8")
    runtime = Path("app/prepare_worker_runtime_v1.py").read_text(encoding="utf-8")
    assert "HUNTER_ENABLE_PREPARE_WORKER: ${HUNTER_ENABLE_PREPARE_WORKER:-false}" in compose
    assert (
        "MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED: "
        "${MUNSHI_PREPARE_ATS_RUNTIME_START_ENABLED:-false}"
    ) in compose
    assert 'prepare_worker = enabled("HUNTER_ENABLE_PREPARE_WORKER")' in supervisor
    assert '"app.prepare_worker_runtime_v1"' in supervisor
    assert "SUBMISSION_AUTHORITY = False" in runtime
    assert '"automatic_submission_executed": False' in runtime
