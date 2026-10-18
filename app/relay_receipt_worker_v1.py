"""Separate bounded inbox receipt reconciliation lane.

Operates only on existing claimed applications; never discovers new jobs,
never dispatches a submission and never sends mail.
"""
from __future__ import annotations
import argparse
import json
from typing import Any


def run_once(*, limit: int = 5) -> dict[str, Any]:
    from app.relay_application_dispatch_v1 import reconcile_owner_pending
    if not 1 <= limit <= 10:
        raise ValueError("Receipt poll limit must remain between 1 and 10.")
    result = reconcile_owner_pending(limit=limit)
    if result.get("applications_sent") != 0:
        raise RuntimeError("Receipt polling returned unexpected submission authority.")
    from app.application_notifications_runtime_v3 import sync_notification_outbox
    # Durable outbox projection never sends and has no submission authority.
    queued = sync_notification_outbox()
    return {
        "status": "completed",
        "checked": int(result.get("checked") or 0),
        "confirmed": int(result.get("employer_confirmed") or 0),
        "errors": int(result.get("errors") or 0),
        "applications_sent": 0,
        "notifications_queued": int(queued),
    }


def main() -> int:
    parser=argparse.ArgumentParser(description="Read existing managed-inbox receipts once")
    parser.add_argument("--limit",type=int,default=5)
    args=parser.parse_args()
    result=run_once(limit=args.limit)
    print(json.dumps(result,sort_keys=True),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
