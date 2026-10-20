"""Professional application emails: responsive, source-grounded, safe, idempotent."""
import pytest

from app.notification_email_design_v1 import notification_email
from app.application_notifications_v1 import (
    SMTP2GOAdapter, NotificationDeliveryError, SMTP2GO_API_URL,
)


def test_job_specific_html_and_accessible_text(hunter_db):
    from app import database
    with database.get_connection() as conn:
        conn.execute(
            "INSERT INTO jobs(id,job_fingerprint,source,company_name,title) "
            "VALUES(901,'email-style-job','JobSpy/LinkedIn','ElectroCraft Ohio Inc',"
            "'Trainer and HR Coordinator')"
        )
        conn.commit()
    message = {
        "event_kind": "EMPLOYER_EMAIL_CONFIRMED",
        "job_id": 901, "subject": "MUNSHI status",
        "body_text": "EMPLOYER EMAIL CONFIRMED",
    }
    page, plain = notification_email(message)
    assert "ElectroCraft Ohio Inc" in page
    assert "Trainer and HR Coordinator" in page
    assert "Confirmation matched" in plain
    assert "Job reference: 901" in plain
    assert "JobSpy/LinkedIn" in plain
    assert "not an original message from the employer" in plain
    assert "viewport" in page and "Review in MUNSHI" in page
    assert "https://dashboard.munshi.systems/" in page


def test_email_escapes_untrusted_job_fields_and_never_embeds_otp_links():
    message = {"event_kind": "SUBMISSION_VERIFIED", "job_id": 24}
    job = {
        "company": '<script>alert("pwn")</script>',
        "title": "<img src=x onerror=alert(1)> Analyst",
        "origin": "Source & co",
    }
    page, plain = notification_email(message, job_context=job)
    assert "<script>alert" not in page and "<img src=x" not in page
    assert "&lt;script&gt;" in page and "&lt;img" in page
    assert "&amp;" in page
    assert "<script>alert" in plain
    assert "submission receipt" in plain
    assert "OTP" not in page and "verification code" not in page


@pytest.mark.parametrize("kind", [
    "SUBMISSION_VERIFIED", "EMPLOYER_EMAIL_CONFIRMED",
    "RECRUITER", "INTERVIEW", "ASSESSMENT", "REJECTION", "OFFER",
])
def test_every_notifiable_event_has_distinct_precise_copy(kind):
    page, plain = notification_email(
        {"event_kind": kind, "job_id": 24},
        job_context={"company": "Example Co", "title": "People Analyst"},
    )
    assert "Example Co" in page and "People Analyst" in plain
    assert "not an original" in plain
    assert "<!doctype html>" in page
    assert len(page) < 15000


def test_unlisted_event_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        notification_email({"event_kind": "PASSWORD_RESET_LINK"})


def test_smtp2go_dual_body_named_sender_and_http_200_failure(monkeypatch, hunter_db):
    monkeypatch.setenv("MUNSHI_SMTP2GO_API_KEY", "smtp2go-test-key-abcdefghijklmnop")
    monkeypatch.setenv("MUNSHI_SMTP2GO_SENDER", "notifications@munshi.systems")

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": self.data}

    class Client:
        response = {"email_id": "accepted", "succeeded": 1, "failed": 0}
        payload = None
        def post(self, url, json):
            assert url == SMTP2GO_API_URL
            self.payload = json
            return Response(self.response)

    client = Client()
    sender = SMTP2GOAdapter(client=client)
    message = {
        "event_kind": "EMPLOYER_EMAIL_CONFIRMED", "job_id": 19,
        "destination_email": "test@example.com",
        "subject": "MUNSHI: employer email confirmed for Example",
        "body_text": "MUNSHI recorded an email confirmation",
    }
    assert sender.send(message) == "accepted"
    assert client.payload["sender"] == "MUNSHI Notifications <notifications@munshi.systems>"
    assert "<!doctype html>" in client.payload["html_body"]
    assert "text_body" in client.payload
    assert client.payload["to"] == ["test@example.com"]

    client.response = {"succeeded": 0, "failed": 1, "failures": ["no permission"]}
    with pytest.raises(NotificationDeliveryError, match="did not accept"):
        sender.send(message)


def test_sender_has_no_effect_on_one_time_outbox_identity(hunter_db):
    from app import database
    from app import application_notifications_v1 as storage
    storage.ensure_schema()
    with database.get_connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) FROM application_notification_outbox_v1"
        ).fetchone()[0]
    notification_email(
        {"event_kind": "EMPLOYER_EMAIL_CONFIRMED", "job_id": 999},
        job_context={"company": "Test Co", "title": "Test"},
    )
    with database.get_connection() as conn:
        after = conn.execute(
            "SELECT COUNT(*) FROM application_notification_outbox_v1"
        ).fetchone()[0]
    assert after == before
