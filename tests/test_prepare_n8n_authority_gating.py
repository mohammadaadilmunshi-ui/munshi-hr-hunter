from __future__ import annotations

from pathlib import Path

import app.stored_job_n8n_worker as worker


class _Socket:
    def connect_ex(self, address):
        self.address = address
        return 0

    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        return None


def test_worker_service_port_open_uses_configured_endpoint(monkeypatch):
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        worker,
        "service_endpoint",
        lambda name: ("n8n", 5678) if name == "n8n" else ("hunter", 8000),
    )

    class RecordingSocket(_Socket):
        def connect_ex(self, address):
            seen["address"] = address
            return 0

    monkeypatch.setattr(worker.socket, "socket", RecordingSocket)

    assert worker._service_port_open("n8n") is True
    assert seen["address"] == ("n8n", 5678)


def test_worker_messages_are_authority_neutral():
    source = Path(worker.__file__).read_text(encoding="utf-8")
    assert "offline on port 5678" not in source
    assert "offline on port 8000" not in source
    assert "Localhost callback" not in source
    assert "localhost callback" not in source
    assert source.count('if not _service_port_open("n8n"):') == 2
    assert source.count('if not _service_port_open("fastapi"):') == 2


def test_dashboard_gates_prepare_on_backend_reachability():
    source = Path("app/operations_dashboard.py").read_text(encoding="utf-8")
    assert "prepare_backend_ready_v18 = (" in source
    assert 'n8n_card_state_v17 = "unavailable"' in source
    assert '"Open cloud Prepare"' in source
    assert '"https://dashboard.munshi.systems"' in source
    handler = source.index("from app.stored_job_n8n_worker import start_stored_job_run")
    unavailable = source.index('n8n_card_state_v17 == "unavailable"')
    assert unavailable < handler
    block = source[handler : handler + 3500]
    assert block.count("st.session_state.pop(") >= 3
    assert '"rankings_n8n_starting_job_v17"' in block
