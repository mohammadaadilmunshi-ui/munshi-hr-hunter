from __future__ import annotations

import argparse
import json

from app.provider_adapter_common import run_provider
from app.sources.workday import fetch_workday_board


SOURCE_NAME = "Workday"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run configured public Workday boards")
    parser.add_argument("--max-boards", type=int, default=None)
    parser.add_argument("--run-now", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_provider(SOURCE_NAME, fetch_workday_board, max_boards=args.max_boards)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("success") and result.get("worker_action") == "run":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
