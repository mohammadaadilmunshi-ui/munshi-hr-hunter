#!/usr/bin/env python3
"""Seed only deterministic synthetic fixtures into a positively identified staging DB."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Permit the documented direct script invocation from any current directory.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.database import SCHEMA_SQL, ensure_job_detail_columns, ensure_operational_columns
from app.staging_fixtures import seed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="Explicit staging SQLite path; never defaults to production.")
    parser.add_argument("--apply", action="store_true", help="Write fixtures. Omit for the safe default dry-run.")
    parser.add_argument("--staging-identity", default=os.getenv("MUNSHI_STAGING_IDENTITY"), help="Must be exactly 'staging'.")
    args = parser.parse_args()
    path = Path(args.database).expanduser()
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA_SQL)
        ensure_job_detail_columns(connection)
        ensure_operational_columns(connection)
        outcome = seed(connection, identity=args.staging_identity, database_path=path, dry_run=not args.apply)
    finally:
        connection.close()
    print("staging fixture seed: " + ", ".join(f"{key}={value}" for key, value in sorted(outcome.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
