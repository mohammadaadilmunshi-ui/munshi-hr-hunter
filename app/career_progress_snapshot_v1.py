"""Fast, owner-scoped, read-only totals for the MUNSHI Home progress cards.

Only recorded application stages count. Never infer successful submissions from
preparation, queued actions, attempts or uncertain dispatches. A provider mirror
is not independent employer-receipt evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
import unicodedata

from app.database import get_connection
from app.tenant_foundation import current_owner_readonly

_TRACKED_STAGES = ("APPLIED", "GHOSTED", "INTERVIEWING", "REJECTED", "OFFER")
_NATIVE_SUBMITTED_STATES = ("PROVIDER_SUBMITTED", "EMPLOYER_CONFIRMED")


@dataclass(frozen=True)
class CareerProgress:
    applications_submitted: int | None
    companies_interviewing: int | None


def _company_identity(name: str) -> str:
    """Conservative employer identity, with one verified corporate alias."""
    canonical = unicodedata.normalize("NFKC", str(name)).casefold()
    canonical = re.sub(r"[^\w]+", " ", canonical).strip()
    canonical = " ".join(canonical.split())
    # These two real tracker labels name the same Securitas organization.
    return {
        "securitas security services usa inc": "securitas",
    }.get(canonical, canonical)


def career_progress_snapshot() -> CareerProgress:
    """One lightweight SQLite snapshot; never triggers an external sync or DDL."""
    conn = get_connection()
    try:
        # A busy writer must not freeze the Home page for 30 seconds.
        conn.execute("PRAGMA busy_timeout=1200")
        conn.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND "
                "name IN ('external_application_pipeline_v1',"
                "'application_provider_routes_v1')"
            )
        }
        if "external_application_pipeline_v1" not in tables:
            # Missing mirror is not evidence that zero applications were sent.
            return CareerProgress(None, None)

        owner = current_owner_readonly(conn)
        scope = (owner.tenant_id, owner.user_id, "TSENTA")
        tracked = conn.execute(
            """SELECT COUNT(*) FROM external_application_pipeline_v1
               WHERE tenant_id=? AND user_id=? AND provider=?
                 AND pipeline_stage IN (?,?,?,?,?)""",
            (*scope, *_TRACKED_STAGES),
        ).fetchone()[0]

        # Count a separately recorded native submission only when its exact
        # provider identity does not already exist in the tracked mirror.
        extra = 0
        if "application_provider_routes_v1" in tables:
            extra = conn.execute(
                """SELECT COUNT(*) FROM application_provider_routes_v1 r
                   WHERE r.tenant_id=? AND r.user_id=? AND r.provider=?
                     AND r.state IN (?,?)
                     AND r.provider_application_id_sha256 IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM external_application_pipeline_v1 e
                         WHERE e.tenant_id=r.tenant_id
                           AND e.user_id=r.user_id
                           AND e.provider=r.provider
                           AND e.provider_application_sha256=
                               r.provider_application_id_sha256
                     )""",
                (*scope, *_NATIVE_SUBMITTED_STATES),
            ).fetchone()[0]

        rows = conn.execute(
            """SELECT company_name FROM external_application_pipeline_v1
               WHERE tenant_id=? AND user_id=? AND provider=?
                 AND pipeline_stage='INTERVIEWING'""",
            scope,
        ).fetchall()
        companies = {
            company for (name,) in rows
            if (company := _company_identity(name))
        }
        return CareerProgress(
            applications_submitted=int(tracked) + int(extra),
            companies_interviewing=len(companies),
        )
    finally:
        conn.close()
