from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database, save_setting


MIGRATION_ID = "011_downstream_contract"
CONTRACT_PATH = ROOT_DIR / "config" / "downstream_contract.json"


def migrate() -> dict[str, object]:
    initialize_database()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    required = {
        "source_system", "payload_schema_version", "workflow_target",
        "callback_url", "queue_version", "ats_target_score",
    }
    missing = sorted(required - set(contract))
    if missing:
        raise RuntimeError(f"Downstream contract fields are missing: {missing}")
    save_setting("downstream_contract", contract, changed_by=MIGRATION_ID)
    checksum = hashlib.sha256(Path(__file__).read_bytes() + CONTRACT_PATH.read_bytes()).hexdigest()
    detail = {
        "contract_schema_version": contract["schema_version"],
        "payload_schema_version": contract["payload_schema_version"],
        "queue_version": contract["queue_version"],
        "n8n_mutated": False,
        "duplicate_callback_receipts": True,
    }
    connection = get_connection()
    try:
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
