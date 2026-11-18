from urllib.parse import quote

import pytest

from app.linkedin_apply_button_v7 import parse_apply_button, unwrap_apply_url

POSTING = "https://www.linkedin.com/jobs/view/4470721097"
TARGET = "https://jobs.example.com/apply?jobId=123&returnUrl=%2Fcareers%3Fa%3D1"


def test_hidden_commented_json_and_query_preserved():
    html = '<code id="applyUrl"><!--"' + TARGET.replace("&", "&amp;") + '"--></code>'
    result = parse_apply_button(html, POSTING)
    assert result.employer_apply_url == TARGET
    assert result.status == "CAPTURED"


def test_external_apply_wrapper():
    wrapped = "https://www.linkedin.com/jobs/view/externalApply?url=" + quote(
        TARGET, safe=""
    )
    html = (
        '<a data-tracking-control-name="public_jobs_apply-link-offsite" href="'
        + wrapped
        + '">Apply</a>'
    )
    assert parse_apply_button(html, POSTING).employer_apply_url == TARGET


def test_related_jobs_and_description_links_are_not_apply():
    result = parse_apply_button(
        '<div class="description"><a href="https://jobs.lever.co/other/123">Apply</a></div>',
        POSTING,
    )
    assert result.status == "UNAVAILABLE"
    assert result.employer_apply_url is None


def test_login_modal_is_distinct_from_closed_or_easy_apply():
    result = parse_apply_button(
        '<button>Apply</button><div data-impression-id="public_jobs_apply-link-offsite_contextual-sign-in-modal">Sign in</div>',
        POSTING,
    )
    assert result.status == "AUTH_REQUIRED"
    assert (
        parse_apply_button("No longer accepting applications", POSTING).status
        == "CLOSED"
    )


def test_different_job_identity_rejected():
    result = parse_apply_button(
        '<link rel="canonical" href="https://www.linkedin.com/jobs/view/4470721098"><a class="apply-button" href="'
        + TARGET
        + '">Apply</a>',
        POSTING,
    )
    assert result.status == "IDENTITY_MISMATCH"


def test_conflicting_apply_controls_are_ambiguous():
    html = '<a class="apply-button" href="https://jobs.lever.co/a/123">Apply</a><code id="applyUrl">"https://jobs.lever.co/a/456"</code>'
    assert parse_apply_button(html, POSTING).status == "AMBIGUOUS"


@pytest.mark.parametrize(
    "value",
    [
        "javascript:alert(1)",
        "https://localhost/apply",
        "https://127.0.0.1/apply",
        "https://www.indeed.com/viewjob?jk=abc",
        "https://user:password@jobs.example.com/apply",
        "https://www.linkedin.com/signup?url=https://jobs.lever.co/a/1",
    ],
)
def test_unsafe_destinations_rejected(value):
    assert unwrap_apply_url(value) is None


def test_oversized_html_rejected():
    with pytest.raises(ValueError):
        parse_apply_button("x" * 2_000_001, POSTING)


def test_jobspy_parser_is_upgraded_in_memory_and_keeps_query(monkeypatch):
    from bs4 import BeautifulSoup
    from jobspy.linkedin import LinkedIn
    from app.linkedin_apply_button_v7 import install_jobspy_apply_parser

    original = LinkedIn._parse_job_url_direct
    monkeypatch.setattr(LinkedIn, "_parse_job_url_direct", original)
    assert install_jobspy_apply_parser()
    patched = LinkedIn._parse_job_url_direct
    assert install_jobspy_apply_parser()
    assert LinkedIn._parse_job_url_direct is patched
    soup = BeautifulSoup(
        '<a class="apply-button" href="' + TARGET.replace("&", "&amp;") + '">Apply</a>',
        "html.parser",
    )
    assert patched(None, soup) == TARGET


def test_normalizer_preserves_query_required_for_exact_job():
    from app.jobspy_board_common import normalize_jobspy_row

    row = {
        "id": "li-4470721097",
        "title": "HR Analyst",
        "company": "Example",
        "job_url": POSTING,
        "job_url_direct": TARGET,
        "description": "A role",
    }
    assert normalize_jobspy_row(row, "linkedin")["apply_url"] == TARGET


def test_external_redirect_is_never_followed():
    from app.linkedin_apply_button_v7 import fetch_apply_button

    class Response:
        status_code = 302
        headers = {"Location": TARGET}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    assert fetch_apply_button(POSTING, session=session).status == "AUTH_REQUIRED"
    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False
