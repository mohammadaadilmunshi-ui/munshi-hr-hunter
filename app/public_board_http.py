"""Bounded, credential-free HTTPS reads of explicitly approved public boards.

Reuse the exact-IP/TLS transport from employer link recovery. No proxies,
cookies, SDKs, credential files, application forms, or automatic redirects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import time
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from app.discovery_policy import require_free_url
from app.linkedin_first_party_careers_v3 import strict_public_hostname
from app.linkedin_official_site_fetch_v3 import _PinnedHTTPSConnection, validated_public_addresses

USER_AGENT = "MUNSHI-Public-Jobs/1.0"


class PublicBoardError(RuntimeError):
    def __init__(self, reason: str, *, status: int = 0, retry_after_seconds: int = 0):
        self.status = status
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"{reason} HTTP {status}; retry_after_seconds={retry_after_seconds}")


def retry_after_seconds(value: str, *, now: datetime | None = None) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0, int((parsed - (now or datetime.now(timezone.utc))).total_seconds()) + 1)
        except (TypeError, ValueError, OverflowError):
            return 0


def validate_board_url(url: str, approved_hosts: set[str]) -> str:
    require_free_url(url)
    try:
        p = urlsplit(url)
        host = strict_public_hostname(p.hostname or "")
        if (p.scheme != "https" or not host or host not in approved_hosts
                or p.username or p.password or p.port not in (None, 443)
                or p.fragment or len(url) > 4096
                or any(c in url for c in ("\\", "\r", "\n", "\x00"))):
            raise ValueError("Unapproved public board URL")
    except ValueError:
        raise ValueError("Unapproved public board URL") from None
    return urlunsplit(("https", host, p.path or "/", p.query, ""))


@dataclass(frozen=True)
class BoardResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]
    url: str

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.content)


class PublicBoardClient:
    def __init__(self, approved_hosts: set[str], *, max_requests: int = 8,
                 timeout: int = 15, time_budget: float = 90, max_bytes: int = 3_000_000,
                 dns_lookup: Callable | None = None, connection_factory: Callable | None = None):
        self.hosts = approved_hosts
        self.max_requests = min(max(1, max_requests), 100)
        self.timeout = min(max(1, timeout), 30)
        self.deadline = time.monotonic() + min(max(1, time_budget), 300)
        self.max_bytes = min(max(1, max_bytes), 10_000_000)
        self.requests = 0
        self.dns_lookup = dns_lookup
        self.connection_factory = connection_factory or _PinnedHTTPSConnection
        self.robots: dict[str, RobotFileParser] = {}
        self.stopped_hosts: dict[str, PublicBoardError] = {}

    def _read(self, url: str) -> BoardResponse:
        url = validate_board_url(url, self.hosts)
        p = urlsplit(url)
        host = str(p.hostname)
        if host in self.stopped_hosts:
            raise self.stopped_hosts[host]
        remaining = self.deadline - time.monotonic()
        if self.requests >= self.max_requests or remaining <= 0:
            raise PublicBoardError("Run request/time budget exhausted")
        kwargs = {"dns_lookup": self.dns_lookup} if self.dns_lookup else {}
        ips = validated_public_addresses(host, **kwargs)
        connection = self.connection_factory(host, ips[0], min(self.timeout, remaining))
        self.requests += 1  # Failed attempts count too.
        try:
            connection.request("GET", p.path + ("?" + p.query if p.query else ""), headers={
                "Host": host, "User-Agent": USER_AGENT, "Accept": "application/json, application/rss+xml, application/xml, text/html",
                "Accept-Encoding": "identity", "Connection": "close",
            })
            response = connection.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            status = int(response.status)
            if status in (401, 403, 429):
                error = PublicBoardError("Provider blocked; stop host", status=status,
                                         retry_after_seconds=retry_after_seconds(headers.get("retry-after", "")))
                self.stopped_hosts[host] = error
                raise error
            length = headers.get("content-length", "0")
            try:
                if int(length) > self.max_bytes:
                    raise PublicBoardError("Response byte budget exhausted")
            except ValueError:
                raise PublicBoardError("Invalid response length") from None
            if hasattr(response, "read1"):
                chunks = []
                size = 0
                while True:
                    remaining = self.deadline - time.monotonic()
                    if remaining <= 0:
                        raise PublicBoardError("Run time budget exhausted")
                    sock = getattr(connection, "sock", None)
                    if sock is not None:
                        sock.settimeout(min(self.timeout, remaining))
                    chunk = response.read1(min(65536, self.max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise PublicBoardError("Response byte budget exhausted")
                body = b"".join(chunks)
            else:
                body = response.read(self.max_bytes + 1)
            if time.monotonic() > self.deadline:
                raise PublicBoardError("Run time budget exhausted")
            if len(body) > self.max_bytes or headers.get("content-encoding", "identity") not in ("", "identity"):
                raise PublicBoardError("Response byte/encoding budget exhausted")
            if status == 200 and any(x in body[:65536].lower() for x in (
                b"verify you are human", b"g-recaptcha", b"cf-chl-", b"unusual traffic",
            )):
                error = PublicBoardError("CAPTCHA; stop host", status=403)
                self.stopped_hosts[host] = error
                raise error
            return BoardResponse(status, body, headers, url)
        finally:
            connection.close()

    def get(self, url: str, *, check_robots: bool = True) -> BoardResponse:
        url = validate_board_url(url, self.hosts)
        host = str(urlsplit(url).hostname)
        if check_robots and host not in self.robots:
            response = self._read(f"https://{host}/robots.txt")
            if response.status_code not in (200, 404, 410):
                raise PublicBoardError("Robots policy unavailable", status=response.status_code)
            parser = RobotFileParser()
            parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            self.robots[host] = parser
        if check_robots:
            parser = self.robots[host]
            if not parser.can_fetch(USER_AGENT, url):
                raise PublicBoardError("Robots disallows board", status=403)
            # A delay instruction is a minimum interval. Do not occupy the
            # worker lane sleeping; defer this contract for manual configuration.
            if parser.crawl_delay(USER_AGENT) or parser.request_rate(USER_AGENT):
                raise PublicBoardError("Robots interval requires scheduled board configuration", status=403)
        response = self._read(url)
        # Reject redirects at the boundary. A redirect never grants host trust.
        if response.status_code != 200:
            raise PublicBoardError("Public board read failed", status=response.status_code,
                                   retry_after_seconds=retry_after_seconds(response.headers.get("retry-after", "")))
        return response
