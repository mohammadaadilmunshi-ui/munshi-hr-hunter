#!/usr/bin/env python3
"""Supervised projection/delivery worker for MUNSHI application notifications.

The worker is deliberately default-off. It never creates submission authority and
never reads ATS verification artifacts. Each cycle projects only verified Hunter
receipt/mail evidence into the durable notification outbox, then asks the outbox
adapter to deliver user-facing status notifications when outbound delivery is enabled.
"""
from __future__ import annotations

import os
import sys
import time

from app.application_notifications_runtime_v2 import notification_cycle


def _enabled(name: str) -> bool:
    return str(os.getenv(name) or "").strip().casefold() in {"1", "true", "yes", "on"}


def _interval_seconds() -> int:
    raw = str(os.getenv("MUNSHI_APPLICATION_NOTIFICATION_WORKER_INTERVAL_SECONDS") or "60").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 60
    return max(10, min(value, 3600))


def run_once() -> dict[str, int]:
    return notification_cycle(limit=50)


def main() -> int:
    once = "--once" in sys.argv[1:]
    if not once and not _enabled("MUNSHI_APPLICATION_NOTIFICATION_WORKER_ENABLED"):
        print("application_notification_worker=disabled")
        return 0

    while True:
        result = run_once()
        print(
            "application_notification_worker "
            f"queued={result['queued']} sent={result['sent']} "
            f"ambiguous={result['ambiguous']} pending={result['pending']}",
            flush=True,
        )
        if once:
            return 0
        time.sleep(_interval_seconds())


if __name__ == "__main__":
    raise SystemExit(main())
