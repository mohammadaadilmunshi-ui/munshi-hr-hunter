"""Schedulable bounded preview of employer destinations for recent LinkedIn jobs.

The default is completely offline. --public-ats-get permits documented public
ATS GETs for existing healthy boards, not LinkedIn access, database writes,
signing, or job applications. No process is scheduled by this script.
"""

from __future__ import annotations

import argparse
import json

from app.linkedin_batch_enrichment_v6 import preview_link_enrichment
from scripts.linkedin_link_resolution_dryrun_v1 import read_only_connection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--max-jobs", type=int, default=50)
    parser.add_argument("--max-boards", type=int, default=12)
    parser.add_argument("--public-ats-get", action="store_true")
    args = parser.parse_args()
    with read_only_connection(args.db) as connection:
        result = preview_link_enrichment(
            connection,
            max_jobs=args.max_jobs,
            max_boards=args.max_boards,
            allow_public_get=args.public_ats_get,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
