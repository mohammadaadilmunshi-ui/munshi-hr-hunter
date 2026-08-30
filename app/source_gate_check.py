from __future__ import annotations

import json

from app.source_runtime import (
    get_source_runtime_state,
)


def main() -> None:
    result = get_source_runtime_state(
        "JobSpy"
    )

    output = {
        "success": True,
        "mode": "source-gate-check",
        "configuration_source": (
            "SQLite dashboard"
        ),
        "network_request_made": False,
        "database_writes": 0,
        "telegram_messages": 0,
        "n8n_calls": 0,
        "source": result,
        "worker_action": (
            "run"
            if result["enabled"]
            and result["due"]
            else "skip"
        ),
    }

    print(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
