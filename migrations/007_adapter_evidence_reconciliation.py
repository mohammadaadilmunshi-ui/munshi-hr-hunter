from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.database import get_connection, initialize_database, save_setting


MIGRATION_ID = "007_adapter_evidence_reconciliation"
MAPPING_PATH = ROOT_DIR / "config" / "adapter_source_mapping.json"


def migrate() -> dict[str, object]:
    initialize_database()
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    save_setting("adapter_source_mapping", mapping, changed_by=MIGRATION_ID)
    checksum = hashlib.sha256(Path(__file__).read_bytes() + MAPPING_PATH.read_bytes()).hexdigest()
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        reconciled = 0
        for item in mapping.get("providers", []):
            source = connection.execute(
                "SELECT * FROM source_health WHERE source_name=?",
                (item["source_name"],),
            ).fetchone()
            if source is None:
                continue
            live_tested = bool(source["last_success_at"])
            fixture_tested = bool(item.get("fixture_evidence"))
            # Preserve direct controlled-probe evidence already stored for new
            # adapters even though they have not been enabled in production.
            coverage = connection.execute(
                "SELECT * FROM adapter_coverage WHERE provider=?",
                (item["provider"],),
            ).fetchone()
            if coverage is None:
                continue
            live_tested = live_tested or bool(coverage["live_tested"])
            fixture_tested = fixture_tested or bool(coverage["fixture_tested"])
            health = str(source["health_status"] or "not_tested")
            if not source["last_run_at"] and live_tested:
                health = str(coverage["health_status"] or "live_verified")
            evidence_note = f"Source evidence: {item['source_name']}."
            if item.get("fixture_evidence"):
                evidence_note += f" Fixture: {item['fixture_evidence']}."
            existing_notes = str(coverage["notes"] or "").strip()
            notes = existing_notes if evidence_note in existing_notes else " ".join(
                value for value in (existing_notes, evidence_note) if value
            )
            connection.execute(
                """
                UPDATE adapter_coverage
                SET fixture_tested=?, live_tested=?, enabled=?, health_status=?,
                    last_verified_at=COALESCE(?, last_verified_at), updated_at=CURRENT_TIMESTAMP,
                    notes=?
                WHERE provider=?
                """,
                (
                    int(fixture_tested), int(live_tested), int(source["enabled"]), health,
                    source["last_success_at"] or source["last_run_at"],
                    notes,
                    item["provider"],
                ),
            )
            reconciled += 1
        detail = {
            "reconciled_providers": reconciled,
            "evidence_rule": "fixture evidence is explicit; live evidence requires source last_success_at or controlled probe",
        }
        connection.execute(
            """
            INSERT INTO schema_migrations(migration_id, checksum, detail_json)
            VALUES (?, ?, ?)
            ON CONFLICT(migration_id) DO UPDATE SET
              applied_at=CURRENT_TIMESTAMP, checksum=excluded.checksum, detail_json=excluded.detail_json
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
