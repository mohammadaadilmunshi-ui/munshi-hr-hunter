from __future__ import annotations

import argparse
import json
from typing import Any

from app.provider_adapter_common import run_provider
from app.sources.public_ats import fetch_public_ats_board, provider_name


def run_public_ats(
    provider: str,
    *,
    max_boards: int | None = None,
) -> dict[str, Any]:
    canonical = provider_name(provider)

    def fetch_board(board, runtime):
        return fetch_public_ats_board(canonical, board, runtime)

    return run_provider(canonical, fetch_board, max_boards=max_boards)


def cli_main(provider: str) -> None:
    canonical = provider_name(provider)
    parser = argparse.ArgumentParser(
        description=f"Run configured public {canonical} boards"
    )
    parser.add_argument("--max-boards", type=int, default=None)
    parser.add_argument("--run-now", action="store_true")
    args, _unknown = parser.parse_known_args()
    result = run_public_ats(canonical, max_boards=args.max_boards)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    if not result.get("success") and result.get("worker_action") == "run":
        raise SystemExit(1)
