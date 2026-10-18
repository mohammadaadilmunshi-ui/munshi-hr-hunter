"""python -m scripts.linkedin_link_recovery_v7 --db DB --checkpoint FILE --public-get"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from app.linkedin_link_recovery_v7 import recover_links


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--public-get", action="store_true")
    parser.add_argument(
        "--retry-blocked",
        action="store_true",
        help="Retry inaccessible pages after access has been restored",
    )
    args = parser.parse_args()
    state = (
        json.loads(args.checkpoint.read_text()) if args.checkpoint.exists() else None
    )

    if state and args.retry_blocked:
        retryable = {
            "AUTH_REQUIRED",
            "AUTH_REQUIRED_NOT_CHECKED",
            "RATE_LIMITED",
            "RATE_LIMITED_NOT_CHECKED",
            "FETCH_FAILED",
            "UNAVAILABLE",
        }
        state["records"] = {
            key: value
            for key, value in state.get("records", {}).items()
            if value.get("status") not in retryable
        }
        state.pop("stop_reason", None)

    def save(result):
        args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.checkpoint.with_suffix(".tmp")
        with temporary.open("w") as handle:
            os.chmod(temporary, 0o600)
            json.dump(result, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(args.checkpoint)

    with sqlite3.connect(
        args.db.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        result = recover_links(
            connection,
            days=args.days,
            limit=args.limit,
            public_get=args.public_get,
            checkpoint=state,
            on_progress=save,
        )
    save(result)
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"records", "unsigned_proposals"}
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
