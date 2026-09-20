#!/usr/bin/env python3
"""Dry-run/apply recent LinkedIn URL repair against the Hunter database."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from app.database import DB_PATH
from app.linkedin_recent_backfill import (
    DEFAULT_LOOKBACK_HOURS,
    apply_recent_linkedin_backfill,
    plan_recent_linkedin_backfill,
)

PRODUCTION_AUTHORIZATION = "AUTHORIZED_LINKEDIN_48H_BACKFILL"


def _truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(f"Hunter database is missing: {path}")
    connection = sqlite3.connect(path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def _assert_apply_allowed(environment: str, authorization: str | None) -> None:
    if environment == "production":
        if authorization != PRODUCTION_AUTHORIZATION:
            raise PermissionError(
                "Production LinkedIn backfill requires explicit authorization."
            )
        if _truthy("CLOUD_SHADOW_MODE"):
            raise RuntimeError("Production backfill refuses CLOUD_SHADOW_MODE=true.")
    elif environment == "staging":
        if not _truthy("CLOUD_SHADOW_MODE"):
            raise RuntimeError("Staging backfill requires CLOUD_SHADOW_MODE=true.")
        if _truthy("PRODUCTION_STATE_IMPORTED"):
            raise RuntimeError("Staging backfill refuses imported production state.")
    else:
        raise ValueError(f"Unsupported environment: {environment}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=str(DB_PATH))
    parser.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument(
        "--environment",
        choices=("staging", "production"),
        required=True,
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--authorization")
    parser.add_argument("--json-output")
    args = parser.parse_args()

    if args.hours != 48:
        raise SystemExit("This controlled repair is currently pinned to exactly 48 hours.")

    path = Path(args.db_path).expanduser().resolve()
    connection = _connect(path)
    try:
        if args.apply:
            _assert_apply_allowed(args.environment, args.authorization)
            result = apply_recent_linkedin_backfill(
                connection,
                lookback_hours=args.hours,
                actor=f"linkedin-48h-backfill:{args.environment}",
            )
            mode = "APPLY"
        else:
            result = plan_recent_linkedin_backfill(
                connection,
                lookback_hours=args.hours,
            )
            mode = "DRY_RUN"
    finally:
        connection.close()

    payload = {
        "mode": mode,
        "environment": args.environment,
        **result,
    }
    rendered = json.dumps(payload, sort_keys=True, indent=2)
    if args.json_output:
        output = Path(args.json_output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    print(f"LINKEDIN_48H_BACKFILL_MODE={mode}")
    print(f"LINKEDIN_48H_SCANNED={result['counts']['scanned']}")
    print(f"LINKEDIN_48H_REPAIRED={result['counts']['repaired']}")
    print(f"LINKEDIN_48H_QUARANTINED={result['counts']['quarantined']}")
    print(f"LINKEDIN_48H_CHANGED={result['counts']['changed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
