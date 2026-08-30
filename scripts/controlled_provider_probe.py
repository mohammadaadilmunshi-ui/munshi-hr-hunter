from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_setting
from app.sources.bamboohr import fetch_bamboohr_board
from app.sources.workday import fetch_workday_board
from app.targeting import filter_jobs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded read-only public ATS probe")
    parser.add_argument("provider", choices=("workday", "bamboohr"))
    parser.add_argument("--company", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--site-name")
    parser.add_argument("--board-url", required=True)
    parser.add_argument("--careers-url")
    parser.add_argument("--max-jobs", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    max_jobs = max(1, min(int(args.max_jobs), 5))
    runtime = dict(get_setting("provider_runtime", {}) or {})
    runtime.update(
        {
            "page_size": max_jobs,
            "max_pages_per_board": 1,
            "max_jobs_per_board": max_jobs,
            "fetch_job_details": True,
            "max_detail_requests_per_board": max_jobs,
        }
    )
    board = {
        "company_name": args.company,
        "tenant": args.tenant,
        "site_name": args.site_name,
        "board_url": args.board_url,
        "careers_url": args.careers_url,
    }
    fetcher = fetch_workday_board if args.provider == "workday" else fetch_bamboohr_board
    fetched = fetcher(board, runtime)
    funnel = filter_jobs(list(fetched["jobs"]))
    samples = [
        {
            "title": row.get("title"),
            "company": row.get("company_name"),
            "location": row.get("location_raw"),
            "primary_category": row.get("primary_category"),
            "reason": (row.get("evidence") or {}).get("reason"),
        }
        for row in funnel.get("_decision_rows", [])
    ]
    output = {
        "success": funnel["accounting_delta"] == 0,
        "provider": args.provider,
        "company": args.company,
        "requests": fetched["requests"],
        "duration_ms": fetched["duration_ms"],
        "raw_normalized": funnel["raw_normalized"],
        "primary_counts": funnel["primary_counts"],
        "accounting_delta": funnel["accounting_delta"],
        "rules_hash": funnel["targeting_rules_hash"],
        "samples": samples,
        "stored": False,
        "telegram_sent": False,
        "n8n_modified": False,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    if not output["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
