from __future__ import annotations

import socket

import pytest

from app.linkedin_first_party_careers_v3 import ApprovedEmployerSite
from app.linkedin_official_site_fetch_v3 import (
    fetch_approved_careers_page,
    validated_public_addresses,
)


def site(**changes):
    return ApprovedEmployerSite(
        company_name="Example",
        official_domain=changes.get("official_domain", "example.com"),
        careers_url=changes.get("careers_url", "https://careers.example.com/jobs"),
        approval_source=changes.get("approval_source", "human_verified"),
        approval_reference="manual-approved-123",
    )


def dns_public(_hostname, _port, *_args):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "127.0.0.1",
        "10.1.1.1",
        "169.254.169.254",
        "::1",
        "192.168.4.5",
        "192.0.2.1",
        "172.16.1.1",
    ],
)
def test_reject_private_reserved_metadata_and_loopback_dns(bad):
    def resolver(_host, _port, *_):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (bad, 443))]

    with pytest.raises(ValueError, match="non-public"):
        validated_public_addresses("careers.example.com", dns_lookup=resolver)


def test_mixed_public_private_dns_fails_closed():
    def resolver(_host, _port, *_):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443)),
        ]

    with pytest.raises(ValueError, match="non-public"):
        validated_public_addresses("careers.example.com", dns_lookup=resolver)


def test_fetch_requires_manual_or_signed_approved_domain():
    with pytest.raises(ValueError, match="Unapproved"):
        fetch_approved_careers_page(
            site(approval_source="search_result"), dns_lookup=dns_public
        )


class FakeResponse:
    def __init__(
        self,
        body=b"<html><title>Careers</title></html>",
        *,
        status=200,
        content_type="text/html",
    ):
        self.body = body
        self.status = status
        self.content_type = content_type

    def getheader(self, name):
        if name == "Content-Type":
            return self.content_type
        if name == "Content-Length":
            return str(len(self.body))
        return None

    def read(self, limit):
        return self.body[:limit]


class FakeConnection:
    def __init__(self, host, ip, timeout, *, response=None):
        self.host, self.ip, self.timeout = host, ip, timeout
        self.response = response or FakeResponse()
        self.requests = []
        self.closed = False

    def request(self, method, path, headers):
        self.requests.append((method, path, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_first_party_fetch_uses_pinned_public_ip_and_get_only():
    holder = []

    def factory(host, ip, timeout):
        conn = FakeConnection(host, ip, timeout)
        holder.append(conn)
        return conn

    html = fetch_approved_careers_page(
        site(), dns_lookup=dns_public, connection_factory=factory
    )
    assert html.startswith("<html>")
    assert holder[0].host == "careers.example.com"
    assert holder[0].ip == "93.184.215.14"
    assert holder[0].timeout == 7
    method, path, headers = holder[0].requests[0]
    assert method == "GET"
    assert path == "/jobs"
    assert headers["Accept-Encoding"] == "identity"
    assert holder[0].closed


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(status=302),
        FakeResponse(content_type="application/json"),
        FakeResponse(body=b"x" * 3_000_001),
    ],
)
def test_redirect_non_html_or_oversize_fails_closed(response):
    holder = []

    def factory(host, ip, timeout):
        conn = FakeConnection(host, ip, timeout, response=response)
        holder.append(conn)
        return conn

    with pytest.raises(ValueError):
        fetch_approved_careers_page(
            site(), dns_lookup=dns_public, connection_factory=factory
        )
    assert holder[0].closed
