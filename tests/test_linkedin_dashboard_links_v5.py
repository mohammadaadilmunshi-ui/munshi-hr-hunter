from __future__ import annotations

from app import product_pages
from tests.test_linkedin_verified_url_evidence_v5 import job


def test_linkedin_card_renders_source_and_disabled_employer_link(monkeypatch):
    hyperlinks = []
    disabled = []
    monkeypatch.setattr(
        product_pages,
        "_linkedin_link_actions",
        lambda row: {
            "is_linkedin": True,
            "linkedin_job_url": row["job_url"],
            "employer_apply_url": None,
        },
    )
    monkeypatch.setattr(
        product_pages.st,
        "link_button",
        lambda label, url, **kwargs: hyperlinks.append((label, url)),
    )
    monkeypatch.setattr(
        product_pages.st,
        "button",
        lambda label, **kwargs: disabled.append((label, kwargs.get("disabled"))),
    )
    product_pages._render_linkedin_source_and_employer(job(), key_prefix="unit")
    assert hyperlinks == [("Open LinkedIn listing", job()["job_url"])]
    assert disabled == [("Employer application link unavailable", True)]


def test_two_verified_links_are_distinct(monkeypatch):
    hyperlinks = []
    employer = "https://job-boards.greenhouse.io/example/jobs/987"
    monkeypatch.setattr(
        product_pages,
        "_linkedin_link_actions",
        lambda row: {
            "is_linkedin": True,
            "linkedin_job_url": row["job_url"],
            "employer_apply_url": employer,
        },
    )
    monkeypatch.setattr(
        product_pages.st,
        "link_button",
        lambda label, url, **kwargs: hyperlinks.append((label, url)),
    )
    product_pages._render_linkedin_source_and_employer(job(), key_prefix="unit")
    assert hyperlinks == [
        ("Open LinkedIn listing", job()["job_url"]),
        ("Open employer application (verified)", employer),
    ]
    assert hyperlinks[0][1] != hyperlinks[1][1]


def test_readonly_fallback_without_migration(monkeypatch):
    from app import database

    class FakeConnection:
        def execute(self, sql, params=None):
            if "sqlite_master" in sql:

                class NoneResult:
                    @staticmethod
                    def fetchone():
                        return None

                return NoneResult()
            raise AssertionError("No job table queries should run")

        def close(self):
            self.closed = True

    connection = FakeConnection()
    monkeypatch.setattr(database, "get_connection", lambda: connection)
    links = product_pages._linkedin_link_actions(job())
    assert connection.closed is True
    assert links["linkedin_job_url"] == job()["job_url"]
    assert links["employer_apply_url"] is None
    assert links["prepare_action"] == "INTERNAL_MUNSHI"


def test_other_sources_keep_existing_dashboard_projection():
    assert product_pages._linkedin_link_actions(job(source="Greenhouse")) == {
        "is_linkedin": False
    }
