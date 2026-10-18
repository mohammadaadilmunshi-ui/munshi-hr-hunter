"""No new application or outgoing message authority in scheduled receipt polling."""
import pytest
from app import relay_receipt_worker_v1 as worker
from app import relay_application_dispatch_v1 as relay


def test_receipt_readback_does_not_dispatch(monkeypatch):
    called=[]
    monkeypatch.setattr(relay,"reconcile_owner_pending",
                        lambda *,limit: called.append(limit) or {
                            "checked":3,"employer_confirmed":1,
                            "errors":0,"applications_sent":0})
    from app import application_notifications_runtime_v3 as notifications
    monkeypatch.setattr(notifications, "sync_notification_outbox", lambda: 2)
    assert worker.run_once(limit=3)=={
        "status":"completed","checked":3,
        "confirmed":1,"errors":0,"applications_sent":0,
        "notifications_queued":2}
    assert called==[3]

def test_receipt_readback_rejects_unexpected_submit_and_large_batch(monkeypatch):
    monkeypatch.setattr(relay,"reconcile_owner_pending", lambda *,limit:
                        {"applications_sent":1})
    with pytest.raises(RuntimeError):
        worker.run_once(limit=1)
    with pytest.raises(ValueError):
        worker.run_once(limit=11)

def test_supervisor_polls_separately_from_prepare_worker():
    from pathlib import Path
    src=(Path(__file__).resolve().parent.parent/"docker/hunter-supervisor.py").read_text()
    assert 'Lane("employer-receipt-readback"' in src
    assert 'interval=max(300,' in src
    assert 'MUNSHI_PREPARE_RUNTIME_WORKER_ENABLED' in src
