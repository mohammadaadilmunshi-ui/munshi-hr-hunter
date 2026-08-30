from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database, save_setting


MIGRATION_ID = "005_verified_provider_boards"
CONFIG_PATH = ROOT_DIR / "config" / "verified_provider_boards.json"


def migrate() -> dict[str, object]:
    initialize_database()
    catalog = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    checksum = hashlib.sha256(Path(__file__).read_bytes() + CONFIG_PATH.read_bytes()).hexdigest()
    save_setting("verified_provider_boards", catalog, changed_by=MIGRATION_ID)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        counts: dict[str, int] = {}
        for board in catalog.get("boards", []):
            connection.execute(
                """
                INSERT INTO provider_board_registry (
                    company_name, provider, tenant, site_name, board_url,
                    careers_url, us_relevance, enabled, priority_weight,
                    last_verified_at, health_status, last_job_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, company_name, tenant, site_name) DO UPDATE SET
                    board_url=excluded.board_url,
                    careers_url=excluded.careers_url,
                    us_relevance=excluded.us_relevance,
                    enabled=excluded.enabled,
                    priority_weight=excluded.priority_weight,
                    last_verified_at=excluded.last_verified_at,
                    health_status=excluded.health_status,
                    last_job_count=excluded.last_job_count,
                    notes=excluded.notes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    board["company_name"], board["provider"], board.get("tenant"),
                    board.get("site_name"), board.get("board_url"), board.get("careers_url"),
                    board.get("us_relevance", "unknown"), int(bool(board.get("enabled"))),
                    int(board.get("priority_weight") or 0), now,
                    board.get("health_status", "live_verified"),
                    int(board.get("last_job_count") or 0), board.get("notes"),
                ),
            )
            provider = str(board["provider"])
            counts[provider] = counts.get(provider, 0) + 1
        for provider, board_count in counts.items():
            connection.execute(
                """
                UPDATE adapter_coverage
                SET implemented=1, fixture_tested=1, live_tested=1,
                    enabled=0, health_status='live_verified',
                    us_board_count=?, blocked_reason=NULL,
                    last_verified_at=?, updated_at=CURRENT_TIMESTAMP
                WHERE provider=?
                """,
                (board_count, now, provider),
            )
        detail = {"providers": sorted(counts), "board_count": sum(counts.values()), "enabled": False}
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id, checksum, detail_json)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
                applied_at=CURRENT_TIMESTAMP,
                checksum=excluded.checksum,
                detail_json=excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(detail, sort_keys=True)),
        )
        connection.commit()
        return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
