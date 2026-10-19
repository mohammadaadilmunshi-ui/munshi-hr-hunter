"""Opt-in, bounded, privacy-minimized MUNSHI Relay learning cycle.

Run once from an external scheduler *after* the owner authorizes the Netcup
MUNSHI Relay backend. No background daemon, network access, or submit permission
is created by importing this module.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any

from app import tsenta_learning_cohort_v1 as cohort
from app import tsenta_learning_sync_v1 as sync
from app import tsenta_oauth_v1 as oauth

ENABLED_ENV = "MUNSHI_TSENTA_LEARNING_AUTO_READONLY_ENABLED"
DEFAULT_PAGES = 5
MAX_PAGES = 20


def learning_cycle(
    *, pages: int = DEFAULT_PAGES, limit: int = 15, start_page: int = 1,
) -> dict[str, Any]:
    """Read a bounded window, tracking its next historical page explicitly."""
    if (
        not 1 <= pages <= MAX_PAGES
        or not 1 <= limit <= sync.MAX_BATCH
        or not 1 <= start_page <= sync.MAX_PAGE
        or start_page + pages - 1 > sync.MAX_PAGE
    ):
        raise ValueError("Read-only learning pages/limits are out of bounds.")

    report: dict[str, Any] = {
        "version": "munshi-tsenta-postdeploy-learning-cycle-v1",
        "mode": "READ_ONLY",
        "enabled": False,
        "status": "DISABLED",
        "pages_checked": 0,
        "provider_applications_seen": 0,
        "new_observations": 0,
        "replayed_observations": 0,
        "provider_submissions_started": 0,
        "native_auto_upgrades_applied": 0,
        "history_window_complete": False,
        "history_page_cap_reached": False,
        "history_start_page": start_page,
        "history_next_page": start_page,
    }
    if str(os.getenv(ENABLED_ENV) or "").strip().lower() not in {"true", "1", "yes"}:
        return report
    connection = oauth.status()
    if connection.get("authorized") is not True or connection.get("profile_read_verified") is not True:
        report["status"] = "BACKEND_TSENTA_AUTH_REQUIRED"
        return report

    report["enabled"] = True
    report["status"] = "RUNNING"
    for page in range(start_page, start_page + pages):
        # Uses authenticated read-only MCP inventory/status/review tools and
        # persists only sanitized, owner-scoped observation records.
        result = sync.sync_authorized_learning(page=page, limit=limit)
        report["pages_checked"] += 1
        report["provider_applications_seen"] += int(result["distinct_applications_checked"])
        report["new_observations"] += int(result["new_observation_rows"])
        report["replayed_observations"] += int(result["replayed_observation_rows"])
        if int(result["listed_applications"]) < limit:
            report["history_window_complete"] = True
            report["history_next_page"] = 1
            break
        report["history_next_page"] = min(page + 1, sync.MAX_PAGE)
    if (
        not report["history_window_complete"]
        and start_page + pages - 1 == sync.MAX_PAGE
    ):
        report["history_page_cap_reached"] = True
    # A partial bounded synchronization never claims all historical records
    # were reached. The next scheduled full-window cycle can look further.
    snapshot = cohort.learning_cohort_snapshot()
    report["status"] = "COMPLETED_READ_ONLY"
    report["learning_phase"] = str(snapshot.get("phase") or "UNAVAILABLE")[:64]
    report["processed_applications"] = int(snapshot.get("processed_applications_total") or 0)
    report["cohort_processed"] = int(snapshot.get("cohort_processed") or 0)
    report["ats_platforms"] = int(snapshot.get("unique_ats_in_cohort") or 0)
    report["shadow_candidates"] = len(snapshot.get("shadow_improvement_candidates") or [])
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in MUNSHI Relay read-only learning iteration")
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGES)
    parser.add_argument("--start-page", type=int, default=1)
    args = parser.parse_args()
    try:
        result = learning_cycle(pages=args.pages, start_page=args.start_page)
    except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError, KeyError):
        # No provider errors, secret-bearing messages, or raw application IDs.
        print(json.dumps({
            "status": "SAFE_ABORT",
            "provider_submissions_started": 0,
            "native_auto_upgrades_applied": 0,
        }))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"DISABLED", "COMPLETED_READ_ONLY"} else 2


if __name__ == "__main__":
    sys.exit(main())
