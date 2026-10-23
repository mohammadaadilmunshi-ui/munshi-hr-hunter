"""Run approved public BreezyHR employer boards through canonical MUNSHI filters."""
from __future__ import annotations

import argparse
import json

from app.provider_adapter_common import run_provider
from app.sources.breezyhr import fetch_breezy_board


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--max-boards", type=int, default=None)
    args = parser.parse_args()
    result = run_provider("BreezyHR", fetch_breezy_board,
                          max_boards=args.max_boards, no_store=args.no_store)
    print(json.dumps(result, ensure_ascii=False, default=str))
    if not result.get("success") and result.get("worker_action") == "run":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
