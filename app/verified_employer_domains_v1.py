"""Reviewed first-party email-sender domains, scoped to an exact ATS board.

A public-site lookup is NOT safe to run on every employer email. Instead,
a narrowly reviewed, version-controlled manifest records the first-party
sender identity and exact employer ATS board. This data alone cannot verify
a receipt: provider-linked ID hash, SES origin, time and explicit acknowledgment
checks in relay_inbox_receipt_v1 remain mandatory.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "verified_employer_domains_v1.json"
_DOMAIN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*\.)+[a-z]{2,24}$")


@lru_cache(maxsize=1)
def _reviewed() -> tuple[tuple[str, str, str], ...]:
    """Fail closed on missing, malformed, or modified data (no external IO)."""
    try:
        payload = json.loads(_MANIFEST.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            return ()
        rows = payload["reviewed_employers"]
        if not isinstance(rows, list) or len(rows) > 100:
            return ()
        approved = []
        for row in rows:
            if not isinstance(row, dict):
                return ()
            company = row["company_name"]
            domain = row["sender_domain"]
            ats_origin = row["ats_board_origin"]
            if not isinstance(company, str) or not 2 <= len(company) <= 140:
                return ()
            if not isinstance(domain, str) or not _DOMAIN.fullmatch(domain):
                return ()
            if not isinstance(ats_origin, str):
                return ()
            parsed = urlsplit(ats_origin)
            if (parsed.scheme != "https" or parsed.username or parsed.password
                    or not parsed.hostname or parsed.query or parsed.fragment):
                return ()
            if parsed.hostname != "job-boards.greenhouse.io":
                return ()
            board = parsed.path.rstrip("/")
            if not re.fullmatch(r"/[a-z0-9-]{3,100}", board):
                return ()
            approved.append((company.casefold(), domain, ats_origin.rstrip("/")))
        if len({x[0] for x in approved}) != len(approved):
            return ()
        return tuple(approved)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ()


def reviewed_sender_matches(
    *,
    company: str,
    sender_domain: str,
    employer_application_url: str | None,
    source: str,
    linked_application_id: object,
) -> bool:
    """Only an SES-authenticated, provider-ID-linked message can use this path."""
    if str(source or "").strip().upper() != "SES":
        return False
    if not isinstance(linked_application_id, str) or not linked_application_id.strip():
        return False
    try:
        ats = urlsplit(str(employer_application_url or ""))
        if ats.scheme != "https" or ats.username or ats.password or ats.query:
            return False
        hostname = ats.hostname
    except ValueError:
        return False
    if hostname != "job-boards.greenhouse.io":
        return False
    for exact_company, exact_domain, reviewed_origin in _reviewed():
        if (str(company or "").strip().casefold() == exact_company
                and str(sender_domain or "").strip().casefold() == exact_domain
                and str(employer_application_url or "").startswith(reviewed_origin + "/jobs/")):
            return True
    return False
