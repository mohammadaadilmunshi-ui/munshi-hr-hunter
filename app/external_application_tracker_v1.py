"""Owner-scoped, read-only provider tracker mirror for MUNSHI Application Center.

This is a display projection, not an application execution or receipt authority.
Raw provider identifiers, inbox content, notes, answers and resumes are never stored.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.database import get_connection
from app.tenant_foundation import current_owner, current_owner_readonly
from app.tenant_foundation import ensure_schema as ensure_tenant_schema
from app.tsenta_mcp_adapter_v1 import _unwrap

PROVIDER = "TSENTA"
STAGES = frozenset({"APPLIED", "GHOSTED", "INTERVIEWING", "REJECTED", "OFFER"})
PAGE_SIZE = 10  # Authenticated provider schema caps list_tracked_applications at 10.
MAX_PAGES = 120  # Bounded history scan: 1,200 rows, with explicit truncation feedback.
HISTORY_START_DATE = "2020-01-01"  # Explicitly include history beyond the provider's 6-month default.
_TEXT_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS external_application_pipeline_v1 (
        tenant_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        provider TEXT NOT NULL CHECK(provider='TSENTA'),
        external_key TEXT NOT NULL,
        provider_application_sha256 TEXT,
        company_name TEXT NOT NULL,
        position_title TEXT NOT NULL,
        pipeline_stage TEXT NOT NULL,
        date_applied TEXT,
        status_history_json TEXT NOT NULL DEFAULT '[]',
        observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant_id, user_id, provider, external_key)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_external_application_pipeline_owner
       ON external_application_pipeline_v1
       (tenant_id, user_id, provider, pipeline_stage, date_applied DESC)""",
)


def _digest(raw: str, *, namespace: str = "") -> str:
    return hashlib.sha256((namespace + raw).encode("utf-8")).hexdigest()


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return _TEXT_CONTROL.sub(" ", value).strip()[:limit]


def _date(value: Any) -> str | None:
    candidate = _safe_text(value, 40)
    # Preserve the provider's ISO precision; do not infer dates or time zones.
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ][0-9:.+\-Z]{5,35})?", candidate):
        return None
    return candidate


def _history(raw: Any) -> str:
    events: list[dict[str, str]] = []
    if isinstance(raw, list):
        for event in raw[:40]:
            if not isinstance(event, Mapping):
                continue
            stage = _safe_text(
                event.get("status") or event.get("toStatus") or event.get("stage"), 32
            ).upper()
            if stage not in STAGES:
                continue
            when = _date(
                event.get("changedAt") or event.get("timestamp")
                or event.get("createdAt") or event.get("at")
            )
            events.append({"status": stage, "at": when or ""})
    return json.dumps(events, separators=(",", ":"))


def normalize_tracked_application(record: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_id = record.get("id")
    if not isinstance(raw_id, str) or not 1 <= len(raw_id) <= 256:
        return None
    company = _safe_text(record.get("companyName"), 240)
    title = _safe_text(record.get("positionTitle"), 240)
    if not company or not title:
        return None
    stage = _safe_text(record.get("status"), 32).upper()
    if stage not in STAGES:
        return None  # Never convert an unknown state into "submitted".
    application_id = record.get("applicationId")
    provider_hash = (
        _digest(application_id)
        if isinstance(application_id, str) and 1 <= len(application_id) <= 256
        else None
    )
    return {
        "external_key": _digest(raw_id, namespace="tracked:"),
        "provider_application_sha256": provider_hash,
        "company_name": company,
        "position_title": title,
        "pipeline_stage": stage,
        "date_applied": _date(record.get("dateApplied")),
        "status_history_json": _history(record.get("statusHistory")),
    }


def _rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for container in (payload, payload.get("data")):
        if isinstance(container, list):
            value = container
        elif isinstance(container, Mapping):
            value = next(
                (container[name] for name in
                 ("applications", "trackedApplications", "rows", "items", "results")
                 if isinstance(container.get(name), list)),
                None,
            )
        else:
            continue
        if isinstance(value, list):
            if len(value) > PAGE_SIZE:
                raise ValueError("Provider tracker exceeded bounded page size.")
            return [dict(row) for row in value if isinstance(row, Mapping)]
    raise TypeError("Provider tracker response shape has not been verified.")


def ensure_schema(connection: sqlite3.Connection) -> None:
    ensure_tenant_schema(connection)
    for statement in _SCHEMA:
        connection.execute(statement)


def persist_tracked_rows(records: list[Mapping[str, Any]]) -> dict[str, int]:
    normalized = [row for record in records
                  if (row := normalize_tracked_application(record)) is not None]
    conn = get_connection()
    try:
        ensure_schema(conn)
        owner = current_owner(conn)
        conn.commit()
        inserted = updated = 0
        conn.execute("BEGIN IMMEDIATE")
        for row in normalized:
            existed = conn.execute(
                """SELECT 1 FROM external_application_pipeline_v1
                   WHERE tenant_id=? AND user_id=? AND provider=? AND external_key=?""",
                (owner.tenant_id, owner.user_id, PROVIDER, row["external_key"]),
            ).fetchone()
            conn.execute(
                """INSERT INTO external_application_pipeline_v1(
                    tenant_id,user_id,provider,external_key,
                    provider_application_sha256,company_name,position_title,
                    pipeline_stage,date_applied,status_history_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(tenant_id,user_id,provider,external_key) DO UPDATE SET
                    provider_application_sha256=COALESCE(
                        excluded.provider_application_sha256,
                        external_application_pipeline_v1.provider_application_sha256),
                    company_name=excluded.company_name,
                    position_title=excluded.position_title,
                    pipeline_stage=excluded.pipeline_stage,
                    date_applied=COALESCE(excluded.date_applied,
                                          external_application_pipeline_v1.date_applied),
                    status_history_json=excluded.status_history_json,
                    observed_at=CURRENT_TIMESTAMP""",
                (owner.tenant_id, owner.user_id, PROVIDER, row["external_key"],
                 row["provider_application_sha256"], row["company_name"],
                 row["position_title"], row["pipeline_stage"],
                 row["date_applied"], row["status_history_json"]),
            )
            if existed:
                updated += 1
            else:
                inserted += 1
        conn.commit()
        return {"inserted": inserted, "updated": updated, "skipped": len(records) - len(normalized)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sync_tracker_from_mcp(
    *,
    mcp: Any,
    max_pages: int = 10,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> dict[str, Any]:
    """Bounded full-history provider reads; never apply, retry or update."""
    if not 1 <= int(max_pages) <= MAX_PAGES:
        raise ValueError("Tracker sync must use bounded pagination.")
    inserted = updated = skipped = checked = 0
    provider_total: int | None = None
    complete = False
    seen_pages: set[tuple[str, ...]] = set()
    for page in range(1, int(max_pages) + 1):
        payload = _unwrap(
            mcp.call_read("list_tracked_applications", {
                "page": page,
                "limit": PAGE_SIZE,
                "startDate": HISTORY_START_DATE,
            }),
            tool_name="list_tracked_applications",
        )
        if not isinstance(payload, Mapping):
            raise TypeError("Provider tracker returned an unsupported payload.")
        pagination = payload.get("pagination")
        if isinstance(pagination, Mapping):
            total = pagination.get("total")
            if isinstance(total, int) and not isinstance(total, bool) and 0 <= total <= 1_000_000:
                provider_total = total
        records = _rows(payload)
        page_identity = tuple(str(row.get("id") or "") for row in records)
        if page_identity in seen_pages:
            break  # An ignored page parameter must never become an infinite scan.
        seen_pages.add(page_identity)
        result = persist_tracked_rows(records)
        inserted += result["inserted"]
        updated += result["updated"]
        skipped += result["skipped"]
        checked += len(records)
        if on_progress is not None:
            on_progress(checked, provider_total)
        has_more = pagination.get("hasMore") if isinstance(pagination, Mapping) else None
        if len(records) < PAGE_SIZE:
            complete = has_more is not True
            break
        if has_more is False:
            complete = True
            break
    return {
        "checked": checked, "inserted": inserted, "updated": updated,
        "skipped": skipped, "provider_total": provider_total,
        "complete": complete, "history_start_date": HISTORY_START_DATE,
        "provider_reads_only": True, "employer_receipts_claimed": 0,
    }


def sync_authorized_tracker(
    *,
    max_pages: int = 10,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> dict[str, Any]:
    from app.tsenta_mcp_http_v1 import TsentaMcpReadClient
    from app.tsenta_oauth_v1 import _token_for_current_owner

    with TsentaMcpReadClient(_token_for_current_owner()) as mcp:
        mcp.initialize()
        return sync_tracker_from_mcp(
            mcp=mcp, max_pages=max_pages, on_progress=on_progress,
        )


@dataclass(frozen=True)
class ExternalApplicationView:
    external_key: str
    company: str
    title: str
    canonical_state: str
    human_state: str
    state_group: str
    last_update: str | None
    date_applied: str | None
    status_history: tuple[dict[str, str], ...]
    application_id: str
    job_id: None = None
    archived: bool = False
    match: None = None
    apply_via: str = "Synced application"
    source_platform: str = ""
    next_action: str = "Review application progress"


_LABELS = {
    "APPLIED": ("Applied · confirmation not verified", "submitted"),
    "GHOSTED": ("Awaiting recruiter response", "submitted"),
    "INTERVIEWING": ("Interviewing · provider reported", "interviewing"),
    "REJECTED": ("Closed · declined (tracker reported)", "closed"),
    "OFFER": ("Offer · provider reported", "offers"),
}


def external_application_views(*, limit: int = 500) -> list[ExternalApplicationView]:
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("external_application_pipeline_v1",)
        ).fetchone()
        if not exists:
            return []
        owner = current_owner_readonly(conn)
        # A matching provider application hash proves identical submission identity;
        # company/title similarity alone is never sufficient to hide a record.
        route_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("application_provider_routes_v1",)
        ).fetchone()
        duplicate_guard = (
            """AND NOT EXISTS(
                SELECT 1 FROM application_provider_routes_v1 r
                 WHERE r.tenant_id=e.tenant_id AND r.user_id=e.user_id
                   AND r.provider='TSENTA'
                   AND r.provider_application_id_sha256=e.provider_application_sha256
                   AND e.provider_application_sha256 IS NOT NULL)"""
            if route_exists else ""
        )
        rows = conn.execute(
            f"""SELECT e.external_key,e.company_name,e.position_title,
                       e.pipeline_stage,e.date_applied,e.status_history_json,
                       e.observed_at
                  FROM external_application_pipeline_v1 e
                 WHERE e.tenant_id=? AND e.user_id=? AND e.provider=?
                       {duplicate_guard}
                 ORDER BY COALESCE(e.date_applied,e.observed_at) DESC
                 LIMIT ?""",
            (owner.tenant_id, owner.user_id, PROVIDER, max(1, min(int(limit), 5000))),
        ).fetchall()
        views = []
        for row in rows:
            stage = str(row["pipeline_stage"])
            label, group = _LABELS.get(stage, ("Status pending", "needs_attention"))
            try:
                history = json.loads(row["status_history_json"])
                if not isinstance(history, list):
                    history = []
            except (TypeError, ValueError):
                history = []
            views.append(ExternalApplicationView(
                external_key=str(row["external_key"]),
                company=str(row["company_name"]),
                title=str(row["position_title"]),
                canonical_state=stage,
                human_state=label,
                state_group=group,
                last_update=str(row["observed_at"]),
                date_applied=str(row["date_applied"] or "") or None,
                status_history=tuple(history[:40]),
                application_id="external:" + str(row["external_key"]),
            ))
        return views
    finally:
        conn.close()


def linked_application_stage_for_job(job_id: int) -> dict[str, Any] | None:
    """Provider stage for a native route with the same exact hashed application ID."""
    conn = get_connection()
    try:
        tables = {
            str(row["name"]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name IN ('external_application_pipeline_v1', "
                "'application_provider_routes_v1')"
            ).fetchall()
        }
        if len(tables) != 2:
            return None
        owner = current_owner_readonly(conn)
        row = conn.execute(
            """SELECT e.pipeline_stage,e.date_applied,
                      e.status_history_json,e.observed_at
                 FROM application_provider_routes_v1 r
                 JOIN external_application_pipeline_v1 e
                   ON e.tenant_id=r.tenant_id AND e.user_id=r.user_id
                  AND e.provider='TSENTA'
                  AND e.provider_application_sha256=r.provider_application_id_sha256
                WHERE r.tenant_id=? AND r.user_id=? AND r.job_id=?
                  AND r.provider='TSENTA'
                  AND r.provider_application_id_sha256 IS NOT NULL
                ORDER BY e.observed_at DESC LIMIT 1""",
            (owner.tenant_id, owner.user_id, int(job_id)),
        ).fetchone()
        if not row:
            return None
        stage = str(row["pipeline_stage"])
        label, _ = _LABELS.get(stage, ("Status pending", "needs_attention"))
        try:
            history = json.loads(row["status_history_json"])
        except (ValueError, TypeError):
            history = []
        return {
            "stage": stage,
            "label": label,
            "date_applied": row["date_applied"],
            "observed_at": row["observed_at"],
            "history": history[:40] if isinstance(history, list) else [],
            "employer_receipt_verified": False,
        }
    finally:
        conn.close()
