from __future__ import annotations

from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.linkedin_cross_source_probe_v1 import inspect_candidate


class Response:
    status = 200

    def __init__(self, url, html):
        self._url = url
        self.body = BytesIO(html.encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.body.close()

    def read(self, count):
        return self.body.read(count)

    def geturl(self):
        return self._url


class Client:
    def __init__(self, html="<title>People Analyst | Example Inc</title>", change_target=None):
        self.html = html
        self.change_target = change_target
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request.get_method(), request.full_url, timeout))
        return Response(self.change_target or request.full_url, self.html)


@pytest.mark.parametrize("url,provider", [
    ("https://example.wd5.myworkdayjobs.com/en-US/example/job/123", "Workday"),
    ("https://careers-foo.icims.com/jobs/123/people-analyst/job", "iCIMS"),
    ("https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/12", "Oracle"),
    ("https://jobs.ashbyhq.com/example/123", "Ashby"),
    ("https://job-boards.greenhouse.io/example/jobs/567", "Greenhouse"),
])
def test_known_ats_urls_are_get_only_and_never_promoted(url, provider):
    http = Client()
    result = inspect_candidate(url, title="People Analyst", opener=http)
    assert result["platform"] == provider
    assert result["title_hint_matches"] is True
    assert result["status"] == "TITLE_HINT_FOUND_NEEDS_SIGNED_REVIEW"
    assert result["verified"] is False
    assert result["submission_authorized"] is False
    assert http.calls == [("GET", url, 6)]


@pytest.mark.parametrize("url", [
    "https://attacker.invalid/jobs/people-analyst",
    "https://localhost/secret",
    "http://jobs.myworkdayjobs.com/foobar",
    "https://jobs.myworkdayjobs.com:8443/foobar",
    "https://jobs.myworkdayjobs.com.evil.com/",
    "https://www.linkedin.com/jobs/view/42",
])
def test_rejects_untrusted_url_without_network(url):
    http = Client()
    result = inspect_candidate(url, title="People Analyst", opener=http)
    assert result["status"] == "UNSUPPORTED_OR_UNTRUSTED_HOST"
    assert http.calls == []


def test_http_redirect_is_not_evidence():
    url = "https://example.icims.com/jobs/123/job"
    http = Client(change_target="https://evil.example/phishing")
    result = inspect_candidate(url, title="People Analyst", opener=http)
    assert result["status"] == "REDIRECT_NOT_ALLOWED"
    assert result["verified"] is False


def test_missing_title_and_oversized_page_are_never_verification():
    url = "https://example.myworkdayjobs.com/job/123"
    result = inspect_candidate(url, title="People Analyst", opener=Client("<title>Sign in</title>"))
    assert result["status"] == "PAGE_REACHABLE_NEEDS_SIGNED_REVIEW"
    assert result["verified"] is False
    huge = Client("<title>People Analyst</title>" + "x" * 300000)
    result = inspect_candidate(url, title="People Analyst", opener=huge)
    assert result["status"] == "RESPONSE_TOO_LARGE"


def test_403_does_not_promote_link():
    class Blocked:
        def open(self, request, *, timeout):
            raise HTTPError(request.full_url, 403, "Forbidden", {}, None)
    result = inspect_candidate(
        "https://example.icims.com/jobs/123/job",
        title="People Analyst", opener=Blocked(),
    )
    assert result["status"] == "PAGE_BLOCKED_OR_UNAVAILABLE"
    assert result["submission_authorized"] is False
