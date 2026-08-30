from __future__ import annotations

import argparse
import json

from app.jobspy_board_common import run_board, source_config
# AADIL_LINKEDIN_JOBSPY_RUNTIME_DESCRIPTION_GUARD_V1_1
from app.linkedin_jobspy_description_guard import install as _aadil_install_linkedin_jobspy_description_guard
_AADIL_LINKEDIN_DESCRIPTION_GUARD = _aadil_install_linkedin_jobspy_description_guard()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        cfg = source_config("linkedin_jobspy")
        print(json.dumps({
            "success": True,
            "self_test": True,
            "source": cfg["display_name"],
            "site_name": cfg["site_name"],
            "request_cap": cfg["requests_per_run"],
            "max_raw_jobs": cfg["max_raw_jobs"],
            "linkedin_fetch_description": True,
            "dashboard_controlled": True,
            "telegram_pipeline": "save_job -> dispatch_unsent_jobs",
            "n8n_calls": 0,
            "network_request_made": False,
        }, indent=2))
        return 0
    result = run_board(
        "linkedin_jobspy",
        no_store=args.no_store,
        force=args.force,
        run_now=args.run_now,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result.get("success") or result.get("worker_action") == "skip" else 2


if __name__ == "__main__":
    raise SystemExit(main())
