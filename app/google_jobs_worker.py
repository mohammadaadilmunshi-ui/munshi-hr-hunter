from __future__ import annotations

import argparse
import json
import os

from app.google_jobs_provider import provider_status
from app.jobspy_board_common import run_board


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--max-requests", type=int, default=0)
    args = parser.parse_args()

    if args.max_requests > 0:
        os.environ["AADIL_GOOGLE_TEST_MAX_REQUESTS"] = str(args.max_requests)

    if args.self_test:
        status = provider_status()
        print(json.dumps({
            "success": True,
            "self_test": True,
            "source": "Google Jobs",
            "provider_chain": ["serpapi", "jobspy"],
            "serpapi_available": status["serpapi_available"],
            "credential_source": status["credential_source"],
            "credential_value_logged": False,
            "dashboard_controlled": True,
            "telegram_pipeline": "save/dedupe -> dispatch_unsent_jobs",
            "n8n_calls": 0,
            "network_request_made": False,
        }, indent=2))
        return 0

    result = run_board(
        "google_jobs_jobspy",
        no_store=args.no_store,
        force=args.force,
        run_now=args.run_now,
    )
    print(json.dumps(
        result,
        indent=2,
        ensure_ascii=False,
        default=str,
    ))
    return (
        0
        if result.get("success")
        or result.get("worker_action") == "skip"
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
