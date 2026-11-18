"""Conservative evidence-only exclusion for explicitly unavailable visa sponsorship.

This guard never infers immigration eligibility from citizenship or the title.
Only unambiguous employer restrictions are matched; it is a safety stop for
candidates who may require future sponsorship, not an application answer engine.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_FIELDS = (
    "description_raw", "description", "job_description",
    "work_authorization", "work_authorization_notes",
    "cpt_opt_sponsorship_notes", "manual_job_text",
    "qualifications", "requirements",
)
_PATTERNS = (
    re.compile(
        r"\b(?:unable|cannot|can't|doesn't|do\s+not|does\s+not|"
        r"will\s+not|won't|not\s+able)\s+"
        r"(?:currently\s+|at\s+this\s+time\s+)?"
        r"(?:(?:to|be\s+able\s+to)\s+)?(?:provide|offer|support|sponsor)\b.{0,90}"
        r"\b(?:visa|sponsorship|immigration|work\s+permit|h-?1b)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:visa|employment|immigration|work)\s+sponsorship\s+"
        r"(?:is|will\s+be|are)\s+(?:not\s+available|unavailable|"
        r"not\s+offered|not\s+provided|not\s+supported)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:no|without)\s+(?:employer[- ]|visa[- ])?"
        r"(?:visa\s+)?sponsorship\s+"
        r"(?:available|offered|provided|support|for\s+this\s+role)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:must|required\s+to)\s+be\s+"
        r"(?:legally\s+)?(?:authorized|eligible)\b.{0,110}"
        r"\bwithout\b.{0,70}\b(?:sponsorship|employer[- ]sponsored|visa\s+support)\b",
        re.I,
    ),
)


def explicit_no_future_sponsorship(job: Mapping[str, Any]) -> str | None:
    """Return a stable reason only for an explicit employer sponsorship ban."""
    for field in _FIELDS:
        raw = str(job.get(field) or "").replace("\\-", "-")
        for sentence in re.split(r"(?<=[.!?])\s+|[\n;]+", raw):
            clean = re.sub(r"\s+", " ", sentence).strip()
            if any(pattern.search(clean) for pattern in _PATTERNS):
                return "posting_explicitly_excludes_sponsorship"
    return None
