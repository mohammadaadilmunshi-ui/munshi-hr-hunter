"""Read-only, resumable recovery of an entire recent LinkedIn cohort.

Results are proposals. Only the existing independent signature/evidence importer
may make a destination executable. No application queues or job URLs are changed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.linkedin_apply_button_v7 import fetch_apply_button
from app.linkedin_batch_enrichment_v6 import ATSReadCache
from app.linkedin_employer_careers_v2 import (
    board_binding_from_ats_company_registry,
    resolve_from_known_company_boards,
)
from app.linkedin_employer_link_resolver_v1 import (
    LinkResolution,
    ats_from_url,
    normalize,
)


def recover_links(
    connection: sqlite3.Connection,
    *,
    days: int = 30,
    limit: int = 1000,
    checkpoint: dict[str, Any] | None = None,
    public_get: bool = False,
    fetcher: Any = fetch_apply_button,
    cache: Any = None,
    now: datetime | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    if not 1 <= days <= 31 or not 1 <= limit <= 2000:
        raise ValueError("Recovery window/budget is outside permitted range")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("Recovery requires a timezone-aware timestamp")
    state = dict(checkpoint or {})
    cutoff = state.get("cutoff") or (current - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    snapshot = state.get("snapshot") or current.strftime("%Y-%m-%d %H:%M:%S")
    cohort = [
        dict(row)
        for row in connection.execute(
            "SELECT id,source,company_name,title,location_raw,description_raw,job_url,apply_url,ats_job_id "
            "FROM jobs WHERE lower(coalesce(source,'')) LIKE '%linkedin%' "
            "AND first_seen_at>=? AND first_seen_at<=? ORDER BY id DESC",
            (cutoff, snapshot),
        )
    ]
    identity = hashlib.sha256(
        json.dumps(
            [(j["id"], j["job_url"]) for j in cohort],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if state.get("cohort_sha256") not in (None, identity):
        raise ValueError("Recovery cohort changed; start a new checkpoint")
    state.update(
        contract="munshi-linkedin-recovery-v7",
        cutoff=cutoff,
        snapshot=snapshot,
        cohort_sha256=identity,
        total_jobs=len(cohort),
    )
    mode = "PUBLIC_GET" if public_get else "OFFLINE"
    records = dict(state.get("records") or {}) if state.get("mode") == mode else {}
    state["mode"] = mode
    boards: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute("SELECT * FROM ats_company_registry WHERE enabled=1"):
        board = dict(row)
        if board_binding_from_ats_company_registry(board):
            boards.setdefault(normalize(board["company_name"]), []).append(board)
    ats_cache = cache or ATSReadCache()
    scanned = 0
    consecutive_auth = 0
    linkedin_stopped = state.get("stop_reason") == "LINKEDIN_RATE_LIMITED"
    for job in cohort:
        key = str(job["id"])
        if key in records:
            continue
        if scanned >= limit:
            break
        scanned += 1
        record: dict[str, Any] = {"job_id": job["id"], "status": "OFFLINE_ONLY"}
        if public_get:
            resolution = None
            choices = boards.get(normalize(job["company_name"]), [])
            if choices:
                try:
                    resolution = resolve_from_known_company_boards(
                        job,
                        choices,
                        allow_network=True,
                        provider_opener=ats_cache,
                    )
                    record["official_ats_status"] = resolution.resolution_status
                except (OSError, ValueError, KeyError, TypeError) as error:
                    record["official_ats_status"] = "FETCH_FAILED"
                    record["official_error_type"] = type(error).__name__
            if resolution is not None and resolution.resolution_status == "VERIFIED":
                record.update(
                    status="VERIFIED_PROPOSAL", resolution=resolution.to_dict()
                )
            elif not linkedin_stopped and consecutive_auth < 5:
                capture = fetcher(job["job_url"])
                record["status"] = capture.status
                if capture.employer_apply_url:
                    result = LinkResolution(
                        linkedin_job_url=capture.linkedin_job_url,
                        employer_job_url=capture.employer_apply_url,
                        employer_apply_url=capture.employer_apply_url,
                        ats_platform=ats_from_url(capture.employer_apply_url)
                        or "Employer’s website",
                        resolution_status="VERIFIED",
                        resolution_source="linkedin_apply_button",
                        evidence={
                            **capture.evidence,
                            "identity_evidence": "strong",
                            "needs_employer_confirmation": False,
                        },
                    )
                    record.update(
                        status="CAPTURED_PROPOSAL", resolution=result.to_dict()
                    )
                consecutive_auth = (
                    consecutive_auth + 1 if capture.status == "AUTH_REQUIRED" else 0
                )
                if capture.status == "RATE_LIMITED":
                    state["stop_reason"] = "LINKEDIN_RATE_LIMITED"
                    linkedin_stopped = True
            else:
                # Continue official-board recovery without hammering sign-in walls.
                record["status"] = (
                    "RATE_LIMITED_NOT_CHECKED"
                    if linkedin_stopped
                    else "AUTH_REQUIRED_NOT_CHECKED"
                )
            record["verified_at"] = current.isoformat(timespec="seconds")
        records[key] = record
        if on_progress:
            on_progress({**state, "records": records})
    state["records"] = records
    state["processed_jobs"] = len(records)
    state["remaining_jobs"] = len(cohort) - len(records)
    state["status_counts"] = dict(
        sorted(Counter(r["status"] for r in records.values()).items())
    )
    state["unsigned_proposals"] = [r for r in records.values() if r.get("resolution")]
    state["database_writes"] = 0
    state["applications_submitted"] = 0
    state["requires_signed_import"] = True
    return state
