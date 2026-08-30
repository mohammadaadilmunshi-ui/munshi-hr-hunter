from __future__ import annotations

import json
from pathlib import Path

from app import database
from app.query_planner import select_queries


ROOT = Path(__file__).resolve().parent.parent


def _query_strategy(**updates) -> dict:
    value = json.loads((ROOT / "config" / "query_strategy.json").read_text())
    value.update(updates)
    return value


def _two_query_policy() -> dict:
    return {
        "schema_version": 3,
        "mode": "OPT",
        "eligibility": {
            "country_codes": ["US"],
            "unknown_country_policy": "reject",
            "remote_allowed": True,
            "hybrid_allowed": True,
            "onsite_allowed": True,
        },
        "experience_policy": {},
        "title_only_hard_rejects": [],
        "role_negative_contexts": [],
        "role_families": [
            {"name": "HR CORE", "title_phrases": ["hr analyst"], "queries": ["HR Analyst"]},
            {"name": "HRIS", "title_phrases": ["hris analyst"], "queries": ["HRIS Analyst"]},
        ],
    }


def test_query_rotation_explores_before_adapting(hunter_db) -> None:
    database.save_setting("targeting", _two_query_policy(), changed_by="pytest")
    database.save_setting(
        "query_strategy",
        _query_strategy(
            max_queries_per_source_cycle=1,
            minimum_samples_before_adaptive_weighting=3,
        ),
        changed_by="pytest",
    )
    first = select_queries("Fixture", advance=True)
    second = select_queries("Fixture", advance=True)
    assert first[0]["query"] == "HR Analyst"
    assert second[0]["query"] == "HRIS Analyst"
    assert first[0]["selection_mode"] == "exploration"
    assert second[0]["selection_mode"] == "exploration"


def test_adaptive_weighting_requires_repeated_samples(hunter_db) -> None:
    database.save_setting("targeting", _two_query_policy(), changed_by="pytest")
    database.save_setting(
        "query_strategy",
        _query_strategy(
            max_queries_per_source_cycle=1,
            minimum_samples_before_adaptive_weighting=3,
            weights={
                "new_eligible_rate": 1.0,
                "eligible_rate": 0.0,
                "telegram_rate": 0.0,
                "error_rate_penalty": 0.0,
                "runtime_penalty": 0.0,
            },
        ),
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        for index in range(3):
            connection.execute(
                """
                INSERT INTO query_performance (
                  run_id,source_name,query_name,role_family,request_count,
                  raw_count,normalized_count,eligible_count,new_eligible_count
                ) VALUES (?,?,?,?,1,10,10,?,?)
                """,
                (f"hr-{index}", "Fixture", "HR Analyst", "HR CORE", 4, 3),
            )
            connection.execute(
                """
                INSERT INTO query_performance (
                  run_id,source_name,query_name,role_family,request_count,
                  raw_count,normalized_count,eligible_count,new_eligible_count
                ) VALUES (?,?,?,?,1,10,10,?,?)
                """,
                (f"hris-{index}", "Fixture", "HRIS Analyst", "HRIS", 1, 0),
            )
        connection.commit()
    finally:
        connection.close()
    selected = select_queries("Fixture", advance=False)
    assert selected[0]["query"] == "HR Analyst"
    assert selected[0]["sample_count"] == 3
    assert selected[0]["selection_mode"] == "adaptive"
