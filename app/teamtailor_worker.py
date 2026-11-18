"""Discover from explicitly configured and independently verified public RSS."""
from __future__ import annotations

import argparse
import json

from app.provider_adapter_common import run_provider
from app.sources.teamtailor import fetch_teamtailor_board


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--max-boards", type=int, default=1)
    args = parser.parse_args()
    result = run_provider("Teamtailor", fetch_teamtailor_board,
                          max_boards=args.max_boards, no_store=args.no_store)
    print(json.dumps(result, indent=2, default=str))
    if not result.get("success") and result.get("worker_action") == "run":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
