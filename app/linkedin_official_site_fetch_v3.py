"""Restricted public HTTPS read of an independently approved employer careers site.

No LinkedIn requests, no credentials, no HTTP redirects, no POST, no proxy.
Pin DNS resolution to a vetted public IP and retain hostname-based TLS checks.
Do not invoke this with search-result or description-derived domains.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable
from urllib.parse import urlsplit

from app.linkedin_first_party_careers_v3 import (
    MAX_HTML_BYTES,
    ApprovedEmployerSite,
    validate_approved_site,
)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, ip: str, timeout: int) -> None:
        super().__init__(
            hostname, port=443, timeout=timeout, context=ssl.create_default_context()
        )
        self.pinned_ip = ip

    def connect(self) -> None:
        # SNI and certificate hostname are still the verified corporate host.
        address = (self.pinned_ip, 443)
        plain_sock = socket.create_connection(address, timeout=self.timeout)
        self.sock = self._context.wrap_socket(plain_sock, server_hostname=self.host)


def validated_public_addresses(
    hostname: str,
    *,
    dns_lookup: Callable = socket.getaddrinfo,
) -> tuple[str, ...]:
    """Fail closed if DNS supplies *any* private/reserved/link-local address."""
    try:
        response = dns_lookup(hostname, 443, 0, socket.SOCK_STREAM)
    except OSError as error:
        raise ValueError("Approved employer DNS resolution failed") from error
    addresses: set[str] = set()
    for entry in response:
        raw = str(entry[4][0])
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as error:
            raise ValueError("Employer DNS returned malformed address") from error
        if not ip.is_global:
            raise ValueError("Employer DNS includes a non-public IP")
        addresses.add(str(ip))
    if not addresses:
        raise ValueError("Employer DNS did not provide public addresses")
    return tuple(sorted(addresses))


def fetch_approved_careers_page(
    site: ApprovedEmployerSite,
    *,
    dns_lookup: Callable = socket.getaddrinfo,
    connection_factory: Callable | None = None,
) -> str:
    """Get one static first-party HTML page; no JS, links or redirects followed.

    Intended for a trusted operator-approved domain record only. Approval
    references are labels until backed by a signed/audited registry.
    """
    address = validate_approved_site(site)
    parsed = urlsplit(address)
    host = str(parsed.hostname)
    ips = validated_public_addresses(host, dns_lookup=dns_lookup)
    factory = connection_factory or _PinnedHTTPSConnection
    connection = factory(host, ips[0], 7)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Host": host,
                "Accept": "text/html",
                "Accept-Encoding": "identity",
                "User-Agent": "MUNSHI-FirstParty-Careers/1.0",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(
                "Employer careers fetch was not HTTP 200; redirects prohibited"
            )
        content_type = str(response.getheader("Content-Type") or "").lower()
        if not content_type.startswith(("text/html", "application/xhtml+xml")):
            raise ValueError("Employer response is not HTML")
        try:
            reported_bytes = int(response.getheader("Content-Length") or "0")
        except (TypeError, ValueError):
            reported_bytes = 0
        if reported_bytes > MAX_HTML_BYTES:
            raise ValueError("Employer HTML exceeds bounded inspection size")
        data = response.read(MAX_HTML_BYTES + 1)
        if len(data) > MAX_HTML_BYTES:
            raise ValueError("Employer HTML exceeds bounded inspection size")
    finally:
        connection.close()
    return data.decode("utf-8", errors="replace")
