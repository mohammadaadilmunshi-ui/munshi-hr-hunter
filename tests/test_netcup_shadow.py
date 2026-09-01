from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from app import database
from scripts.validate_netcup_shadow import CANONICAL_SHA256, validate

ROOT = Path(__file__).resolve().parents[1]


def test_netcup_shadow_contract_is_valid() -> None:
    assert validate(ROOT) == []


def test_canonical_workflow_remains_immutable() -> None:
    canonical = ROOT / "n8n/workflows/canonical_hr_hunter_workflow.json"
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == CANONICAL_SHA256


def test_example_contains_placeholders_only() -> None:
    example = (ROOT / ".env.netcup.shadow.example").read_text(encoding="utf-8")
    assert "REPLACE_WITH_SYNTHETIC_SHADOW_SECRET" in example
    assert "REPLACE_WITH_SYNTHETIC_SHADOW_KEY" in example
    assert "TELEGRAM_ENABLED=false" in example
    assert "PRODUCTION_STATE_IMPORTED=false" in example


def test_fresh_database_has_portable_runtime_relations(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "fresh-shadow.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    database.initialize_database()
    connection = sqlite3.connect(path)
    try:
        relations = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        assert {
            "source_random_schedule",
            "source_runtime_truth_v1",
            "telegram_delivery_claims",
        } <= relations
        assert connection.execute(
            "SELECT COUNT(*) FROM source_runtime_truth_v1"
        ).fetchone()[0] >= 0
    finally:
        connection.close()
