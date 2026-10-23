"""Independently correlated managed inbox receipt must enter exactly one outbox."""
from app import application_notifications_runtime_v3 as notes
from app import database


def test_provider_receipt_queues_once_without_sending_or_guessing(hunter_db, monkeypatch):
    notes.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=False,
        employer_confirmation_email_enabled=True,
        communication_email_enabled=False,
    )
    db=database.get_connection()
    try:
        db.execute(
            """CREATE TABLE IF NOT EXISTS application_provider_routes_v1(
                 route_id TEXT,tenant_id TEXT,user_id TEXT,application_id TEXT,
                 job_id INTEGER,provider TEXT,state TEXT,receipt_method TEXT,
                 receipt_evidence_digest TEXT,provider_application_id_sha256 TEXT,
                 employer_confirmed_at TEXT)"""
        )
        db.execute(
            """CREATE TABLE IF NOT EXISTS tsenta_external_dispatch_claims_v1(
                 route_id TEXT,tenant_id TEXT,user_id TEXT,job_id INTEGER,
                 state TEXT,tsenta_application_id_sha256 TEXT)"""
        )
        db.execute("""INSERT OR IGNORE INTO jobs(
                   id,job_fingerprint,source,company_name,title,apply_url)
                   VALUES(8889,'verified-relay-test-8889','test',
                   'Arcadia Labs','People Operations Coordinator',
                   'https://careers.example/job/1')""")
        db.execute(
            """INSERT INTO application_provider_routes_v1 VALUES(
                'route-8889','default','local-owner','application-8889',
                8889,'TSENTA','EMPLOYER_CONFIRMED',
                'CORRELATED_EMPLOYER_EMAIL',?,?,'2026-10-18T19:00:00Z')""",
            ("a"*64,"b"*64)
        )
        db.execute("""INSERT INTO tsenta_external_dispatch_claims_v1
            VALUES('route-8889','default','local-owner',8889,'ACCEPTED',?)""",
            ("b"*64,)
        )
        db.commit()
    finally: db.close()
    assert notes.sync_notification_outbox()==1
    assert notes.sync_notification_outbox()==0
    db=database.get_connection()
    try:
        row=db.execute(
            """SELECT event_kind,job_id,delivery_state,attempt_count,
                      destination_email,subject
               FROM application_notification_outbox_v1
               WHERE job_id=8889""").fetchone()
        assert row["event_kind"]=="EMPLOYER_EMAIL_CONFIRMED"
        assert row["delivery_state"]=="PENDING"
        assert row["attempt_count"]==0
        assert row["destination_email"]=="candidate@example.com"
        assert "Arcadia Labs" in row["subject"]
        db.execute("""UPDATE tsenta_external_dispatch_claims_v1
            SET state='FAILED' WHERE route_id='route-8889'""")
        db.commit()
    finally:db.close()
    assert notes.sync_notification_outbox()==0


def test_provider_sent_is_not_employer_email_confirmation(hunter_db):
    notes.save_preference(
        destination_email="candidate@example.com",
        submission_email_enabled=False,
        employer_confirmation_email_enabled=True,
        communication_email_enabled=False,
    )
    db=database.get_connection()
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS application_provider_routes_v1(
          route_id TEXT,tenant_id TEXT,user_id TEXT,application_id TEXT,
          job_id INTEGER,provider TEXT,state TEXT,receipt_method TEXT,
          receipt_evidence_digest TEXT,provider_application_id_sha256 TEXT,
          employer_confirmed_at TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS tsenta_external_dispatch_claims_v1(
          route_id TEXT,tenant_id TEXT,user_id TEXT,job_id INTEGER,
          state TEXT,tsenta_application_id_sha256 TEXT)""")
        db.execute("""INSERT OR IGNORE INTO jobs(
          id,job_fingerprint,source,company_name,title,apply_url)
          VALUES(8890,'unverified-relay-test','test','Any Employer',
          'Recruitment Analyst','https://boards.example/jobs/1')""")
        db.execute("""INSERT INTO application_provider_routes_v1 VALUES(
          'route-8890','default','local-owner','app-8890',8890,
          'TSENTA','PROVIDER_SUBMITTED',NULL,NULL,?,NULL)""",("c"*64,))
        db.execute("""INSERT INTO tsenta_external_dispatch_claims_v1
          VALUES('route-8890','default','local-owner',8890,'ACCEPTED',?)""",("c"*64,))
        db.commit()
    finally:db.close()
    assert notes.sync_notification_outbox()==0
