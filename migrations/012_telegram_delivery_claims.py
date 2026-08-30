from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database


MIGRATION_ID = "012_telegram_delivery_claims"


def migrate() -> dict[str, object]:
    initialize_database()
    checksum = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    connection = get_connection()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS telegram_delivery_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL UNIQUE,
                claim_token TEXT NOT NULL UNIQUE,
                delivery_state TEXT NOT NULL DEFAULT 'reserved',
                chat_id TEXT,
                message_id INTEGER,
                error_type TEXT,
                reserved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_telegram_delivery_claims_state
            ON telegram_delivery_claims(delivery_state, updated_at);
            """
        )
        detail = {
            "atomic_reservation": True,
            "ambiguous_delivery_state": "uncertain",
            "automatic_retry_of_uncertain_delivery": False,
            "telegram_calls": 0,
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
    finally:
        connection.close()
    return {"migration_id": MIGRATION_ID, "checksum": checksum, **detail}


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2, sort_keys=True))
