from __future__ import annotations

import pytest

from app import database, force_rerun_v1, telegram_audit, telegram_client
from app.telegram_auto_dispatch import attribute_dispatch_to_current_jobs
from app.job_store import save_job


def _job(suffix: str = "1") -> dict[str, object]:
    return {
        "source": "Fixture ATS",
        "source_tier": 1,
        "ats_job_id": f"REQ-TG-{suffix}",
        "company_name": "Example Employer",
        "title": "People Analytics Analyst",
        "location_raw": "Austin, TX",
        "state": "TX",
        "country": "US",
        "remote_type": "Hybrid",
        "salary_raw": "$60,000-$70,000",
        "apply_url": f"https://example.test/telegram-{suffix}",
        "description_raw": "Analyze workforce metrics and maintain HR dashboards.",
        "entry_path": "adapter_discovery",
    }


def _stored_job() -> dict[str, object]:
    connection = database.get_connection()
    try:
        stored = save_job(connection, _job(), actor="fixture_worker")
        row = dict(connection.execute("SELECT * FROM jobs WHERE id=?", (stored["job_id"],)).fetchone())
        connection.commit()
        return row
    finally:
        connection.close()


def test_dispatch_attribution_separates_current_jobs_from_backlog() -> None:
    result = attribute_dispatch_to_current_jobs(
        {
            "telegram_messages_sent": 3,
            "sent": [{"job_id": 10}, {"job_id": 20}, {"job_id": 30}],
        },
        [20, 40],
    )
    assert result == {
        "total_messages": 3,
        "current_run_messages": 1,
        "backlog_messages": 2,
    }


def test_job_card_surfaces_canonical_eligibility_and_operational_context(hunter_db) -> None:
    job = _stored_job()
    card = telegram_client.format_job_card(job)
    assert "People Analytics Analyst" in card
    assert "Example Employer" in card
    assert "Austin, TX" in card
    assert "Fixture ATS" in card
    assert "Eligibility: OPT · United States nationwide" in card
    assert "Why this score" in card


def test_successful_delivery_claim_prevents_duplicate_send(hunter_db, monkeypatch) -> None:
    job = _stored_job()
    calls: list[str] = []

    def request(method, _payload=None):
        calls.append(method)
        return {"ok": True, "result": {"message_id": 456}}

    monkeypatch.setattr(telegram_client, "CHAT_ID", "fixture-chat")
    monkeypatch.setattr(telegram_client, "telegram_request", request)
    assert telegram_client.send_job_card(int(job["id"])) == 456
    with pytest.raises(telegram_client.TelegramDeliveryAlreadyClaimed):
        telegram_client.send_job_card(int(job["id"]))

    connection = database.get_connection()
    try:
        delivery = connection.execute(
            "SELECT delivery_state,message_id FROM telegram_delivery_claims WHERE job_id=?",
            (job["id"],),
        ).fetchone()
        telegram_sent = connection.execute(
            "SELECT telegram_sent FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()[0]
    finally:
        connection.close()
    assert calls == ["sendMessage"]
    assert dict(delivery) == {"delivery_state": "sent", "message_id": 456}
    assert telegram_sent == 1


def test_ambiguous_delivery_is_held_without_automatic_retry(hunter_db, monkeypatch) -> None:
    job = _stored_job()
    calls = 0

    def request(_method, _payload=None):
        nonlocal calls
        calls += 1
        raise TimeoutError("ambiguous fixture timeout")

    monkeypatch.setattr(telegram_client, "CHAT_ID", "fixture-chat")
    monkeypatch.setattr(telegram_client, "telegram_request", request)
    with pytest.raises(TimeoutError):
        telegram_client.send_job_card(int(job["id"]))
    with pytest.raises(telegram_client.TelegramDeliveryAlreadyClaimed):
        telegram_client.send_job_card(int(job["id"]))

    connection = database.get_connection()
    try:
        delivery = connection.execute(
            "SELECT delivery_state,error_type FROM telegram_delivery_claims WHERE job_id=?",
            (job["id"],),
        ).fetchone()
        review_events = connection.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE job_id=? AND event_type='telegram_job_card_delivery_uncertain'
            """,
            (job["id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    assert calls == 1
    assert dict(delivery) == {"delivery_state": "uncertain", "error_type": "TimeoutError"}
    assert review_events == 1


def test_force_rerun_child_does_not_inherit_telegram_delivery_state(hunter_db) -> None:
    parent = _stored_job()
    connection = database.get_connection()
    try:
        connection.execute(
            "UPDATE jobs SET telegram_sent=1, sent_to_n8n=1 WHERE id=?",
            (parent["id"],),
        )
        current = dict(
            connection.execute("SELECT * FROM jobs WHERE id=?", (parent["id"],)).fetchone()
        )
        child_id, _fingerprint = force_rerun_v1._copy_job_as_child(connection, current, 2)
        child = connection.execute(
            "SELECT telegram_sent,sent_to_n8n,status FROM jobs WHERE id=?",
            (child_id,),
        ).fetchone()
    finally:
        connection.close()

    assert dict(child) == {
        "telegram_sent": 0,
        "sent_to_n8n": 0,
        "status": "approved_for_n8n",
    }


def test_telegram_audit_treats_a_later_success_as_sync_recovery(
    hunter_db, monkeypatch
) -> None:
    job = _stored_job()
    monkeypatch.setattr(telegram_audit, "get_connection", database.get_connection)
    connection = database.get_connection()
    try:
        connection.execute("UPDATE jobs SET telegram_sent=1 WHERE id=?", (job["id"],))
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES (?,'telegram_job_card_sent','telegram','completed','{}')
            """,
            (job["id"],),
        )
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES (?,'telegram_card_sync','fixture','failed','{}')
            """,
            (job["id"],),
        )
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES (?,'telegram_card_sync','fixture','completed','{}')
            """,
            (job["id"],),
        )
        connection.commit()
    finally:
        connection.close()

    assert "none missing" in telegram_audit.check_database_cards()
    assert telegram_audit.check_sync_events() == "no unresolved failed synchronization events"

    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO events(job_id,event_type,actor,event_status,payload_json)
            VALUES (?,'telegram_card_sync','fixture','failed','{}')
            """,
            (job["id"],),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(telegram_audit.AuditFailure):
        telegram_audit.check_sync_events()
