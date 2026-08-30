from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database


MIGRATION_ID = "013_source_health_truth"


def migrate() -> dict[str, object]:
    initialize_database()
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        repaired = connection.execute(
            """
            UPDATE source_health
            SET health_status='degraded',
                error_count_last_run=CASE
                    WHEN COALESCE(error_count_last_run,0)=0 THEN 1
                    ELSE error_count_last_run
                END,
                updated_at=CURRENT_TIMESTAMP
            WHERE enabled=1 AND health_status='healthy'
              AND trim(COALESCE(last_error,''))!=''
            """
        ).rowcount
        detail = {
            "healthy_rows_with_current_error_reclassified": int(repaired or 0),
            "policy": "partial_success_with_error_is_degraded",
            "network_requests": 0,
            "n8n_mutated": False,
        }
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id,checksum,detail_json)
            VALUES (?,?,?)
            ON CONFLICT(migration_id) DO UPDATE SET
              applied_at=CURRENT_TIMESTAMP,checksum=excluded.checksum,detail_json=excluded.detail_json
            """,
            (MIGRATION_ID, checksum, json.dumps(detail, sort_keys=True)),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
