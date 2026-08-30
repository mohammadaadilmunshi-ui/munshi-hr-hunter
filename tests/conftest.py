from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import database


@pytest.fixture()
def hunter_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "hunter.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    database.initialize_database()

    root = Path(__file__).resolve().parent.parent
    policy = json.loads(
        (root / "config" / "canonical_targeting_policy.json").read_text(encoding="utf-8")
    )
    policy.update(
        {
            "target_roles": [
                "People Analytics Analyst",
                "HR Operations Analyst",
                "Recruiting Coordinator",
                "Benefits Analyst",
                "Compensation Analyst",
            ],
            "target_tracks": [],
            "hard_reject_keywords": [
                "senior",
                "3 years",
                "must be US citizen",
                "unpaid internship",
            ],
            "company_blacklist": ["Blocked Example"],
            "boosted_keywords": [],
            "company_watchlist": [],
        }
    )
    database.save_setting("targeting", policy, changed_by="pytest")
    database.save_setting(
        "authorization",
        {"authorization_mode": "OPT"},
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        for name, location_type, city, state, purpose, weight in (
            ("United States", "Country", None, None, "eligibility", 0),
            ("New Jersey", "State", None, "NJ", "preference", 20),
            ("Philadelphia", "City", "Philadelphia", "PA", "preference", 20),
            ("New York City", "City", "New York", "NY", "preference", 18),
        ):
            connection.execute(
                """
                INSERT INTO location_rules (
                    location_name, location_type, city, state, country,
                    remote_allowed, hybrid_allowed, onsite_allowed,
                    priority_weight, rule_purpose
                ) VALUES (?, ?, ?, ?, 'US', 1, 1, 1, ?, ?)
                """,
                (name, location_type, city, state, weight, purpose),
            )
        connection.commit()
    finally:
        connection.close()
    return path
