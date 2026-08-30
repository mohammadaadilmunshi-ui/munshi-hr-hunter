from __future__ import annotations

import argparse
import json

from app.provider_adapter_common import run_provider
from app.sources.bamboohr import fetch_bamboohr_board


SOURCE_NAME = "BambooHR"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run configured public BambooHR boards")
    parser.add_argument("--max-boards", type=int, default=None)
    parser.add_argument("--run-now", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_provider(SOURCE_NAME, fetch_bamboohr_board, max_boards=args.max_boards)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("success") and result.get("worker_action") == "run":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
