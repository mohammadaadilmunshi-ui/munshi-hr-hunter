"""Ingestion provenance stays internal; user-visible labels show board only."""
from app.source_provenance import format_adapter_display
from app.application_runs_v1 import _canonical_adapter
from app.notification_email_design_v1 import notification_email


def test_telegram_and_application_summary_hide_scraper_label():
    for input_value, expected in [
        ("JobSpy/indeed", "Indeed"),
        ("JobSpy/linkedin", "LinkedIn"),
        ("Indeed Jobs (JobSpy)", "Indeed"),
        ("LinkedIn Jobs (JobSpy)", "LinkedIn"),
        ("JobSpy", "Job board"),
    ]:
        assert format_adapter_display({"source": input_value}) == expected
        assert _canonical_adapter(input_value) == expected


def test_email_uses_platform_name_only_in_html_and_plain_text():
    for internal, platform in [
        ("JobSpy/indeed", "Indeed"),
        ("JobSpy/linkedin", "LinkedIn"),
        ("JobSpy/glassdoor", "Glassdoor"),
        ("JobSpy/monster", "Monster"),
    ]:
        html_body, text_body = notification_email(
            {"event_kind": "EMPLOYER_EMAIL_CONFIRMED", "job_id": 12},
            job_context={
                "company": "Example Company", "title": "HR Analyst",
                "origin": internal,
            },
        )
        assert "Source: " + platform in html_body
        assert "Source: " + platform in text_body
        assert "JobSpy" not in html_body
        assert "JobSpy" not in text_body
