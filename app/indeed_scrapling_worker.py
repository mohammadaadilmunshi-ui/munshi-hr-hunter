from __future__ import annotations

import argparse
import json

from app.scrapling_adapter_common import run_source


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = run_source(
        "indeed",
        no_store=args.no_store,
        force=args.force,
        smoke=args.smoke,
    )
    print(json.dumps(result, indent=2, default=str))
    status = str(result.get("status") or "")
    return 0 if status in {
        "success", "skipped_active_work", "skipped_locked",
        "skipped_daily_ceiling",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
