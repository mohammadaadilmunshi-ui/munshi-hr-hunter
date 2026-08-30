from __future__ import annotations

import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

from app.presentation_analytics import (
    daily_history,
    decision_view_model,
    explainable_evidence_model,
    humanize_machine_value,
    lifetime_metrics,
    n8n_execution_metrics,
)


def test_machine_values_are_translated_for_primary_presentation() -> None:
    assert humanize_machine_value("REJECT_COMPANY") == "Company exclusion"
    assert humanize_machine_value("confirmed_us_eligible") == "United States eligibility confirmed"
    assert humanize_machine_value("NULL") == "Not available"


def test_decision_view_is_human_readable() -> None:
    record = {
        "primary_decision": "ELIGIBLE",
        "title": "Human Resources Generalist",
        "location_raw": "Port Hueneme, California",
        "remote_type": "On-site",
        "target_track": "HR CORE",
        "telegram_sent": 1,
        "sent_to_n8n": 0,
        "decision_evidence_json": {
            "role": {
                "accepted": True,
                "reason": "configured_role_evidence",
                "matched_phrase": "Human Resources Generalist",
                "target_family": "HR CORE",
                "match_source": "configured_target_role",
            },
            "location": {
                "accepted": True,
                "reason": "confirmed_us_eligible",
                "evidence": {"arrangement": "onsite"},
            },
            "experience": [],
            "hard_requirement": {"rejected": False},
        },
    }
    view = decision_view_model(record)
    assert view["decision"] == "Eligible"
    assert view["role_summary"] == "Target role matched"
    assert view["location_summary"] == "United States eligibility confirmed"
    assert view["delivery_state"] == "Delivered to Telegram"
    assert "_" not in " ".join(str(value) for value in view.values())


def test_explainable_evidence_has_human_layers_and_preserves_links_without_raw_hashes() -> None:
    record = {
        "id": 1171,
        "source": "USAJobs",
        "ats_job_id": "26-46",
        "title": "Human Resources Generalist",
        "company_name": "U.S. Courts",
        "location_raw": "Phoenix, Arizona",
        "country": "US",
        "remote_type": "On-site",
        "primary_decision": "ELIGIBLE",
        "telegram_sent": 1,
        "sent_to_n8n": 0,
        "targeting_rules_version": "3",
        "job_fingerprint": "secret-looking-fingerprint",
        "duplicate_group": "secret-looking-group",
        "secondary_reasons_json": "[]",
        "source_provenance_json": '[{"source":"USAJobs","external_id":"26-46","url":"https://example.test/job/26-46"}]',
        "decision_evidence_json": {
            "reason": "canonical_targeting_match",
            "role": {
                "accepted": True,
                "reason": "configured_role_evidence",
                "matched_phrase": "Human Resources Generalist",
                "target_family": "HR CORE",
                "match_source": "configured_target_role",
            },
            "location": {
                "accepted": True,
                "reason": "confirmed_us_eligible",
                "evidence": {
                    "location_raw": "Phoenix, Arizona",
                    "raw_country": "US",
                    "provider_country": None,
                    "state_evidence": ["state_name:arizona"],
                    "us_city_hints": ["phoenix"],
                    "foreign_country_terms": [],
                    "foreign_city_hints": [],
                    "arrangement": "onsite",
                },
            },
            "experience": [{
                "field": "description_raw",
                "classification": "REQUIRED",
                "minimum_years": 1,
                "maximum_years": None,
                "evidence": "One year of specialized experience is required.",
            }],
            "hard_requirement": {"rejected": False, "configured_phrase_matches": []},
        },
    }
    model = explainable_evidence_model(record)
    facts = dict(model["location"])
    assert facts["Eligibility"] == "United States eligibility confirmed"
    assert facts["Country"] == "United States"
    assert facts["State"] == "Arizona"
    assert facts["City"] == "Phoenix"
    assert facts["Provider country"] == "Not supplied by provider"
    assert facts["Foreign-location indicators"] == "None detected"
    assert model["experience_rows"][0]["Minimum"] == "1 year"
    assert model["experience_rows"][0]["Maximum"] == "Not specified"
    assert dict(model["delivery"])["Telegram"] == "Sent"
    assert dict(model["delivery"])["n8n"] == "Not dispatched"
    assert dict(model["provenance"])["Fingerprint verification"] == "Available"
    assert model["source_links"][0]["url"] == "https://example.test/job/26-46"
    assert dict(model["targeting"])["Matched from"] == "Job title"
    visible = str({key: value for key, value in model.items() if key not in {"source_links", "application_url"}})
    assert "secret-looking" not in visible
    assert "job_fingerprint" not in visible
    assert "rules_hash" not in visible


def test_explainable_rejection_and_legacy_negative_delivery_flag_are_humanized() -> None:
    record = {
        "source": "Dice/direct",
        "title": "HR Generalist",
        "location_raw": "Not specified",
        "country": "US",
        "primary_decision": "REJECT_COMPANY",
        "telegram_sent": -1,
        "sent_to_n8n": 0,
        "secondary_reasons_json": '["company_blacklisted"]',
        "decision_evidence_json": {
            "reason": "company_blacklisted",
            "role": {"accepted": True, "reason": "configured_role_evidence"},
            "location": {
                "accepted": True,
                "reason": "confirmed_us_eligible",
                "evidence": {"raw_country": "US", "foreign_country_terms": []},
            },
            "hard_requirement": {"rejected": False},
        },
    }
    view = decision_view_model(record)
    model = explainable_evidence_model(record)
    assert view["delivery_state"] == "Not delivered"
    assert dict(model["delivery"])["Telegram"] == "Not sent"
    assert dict(model["delivery"])["Additional reasons"] == "Company exclusion"


def test_foreign_and_fail_closed_location_codes_are_presentation_language() -> None:
    assert humanize_machine_value("explicit_non_us_country") == "Outside United States targeting"
    assert humanize_machine_value("country_unknown_fail_closed") == "United States eligibility not confirmed"


def test_n8n_history_is_scoped_to_verified_workflow_and_local_day(tmp_path: Path) -> None:
    database = tmp_path / "n8n.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE workflow_entity(id TEXT PRIMARY KEY,name TEXT,active INTEGER);
        CREATE TABLE execution_entity(
          id INTEGER PRIMARY KEY,workflowId TEXT,finished INTEGER,mode TEXT,
          startedAt TEXT,stoppedAt TEXT,status TEXT,deletedAt TEXT,createdAt TEXT
        );
        INSERT INTO workflow_entity VALUES('canonical','Canonical workflow',1);
        INSERT INTO workflow_entity VALUES('other','Unrelated workflow',1);
        INSERT INTO execution_entity VALUES(1,'canonical',1,'webhook','2026-08-25 01:00:00','2026-08-25 01:01:00','success',NULL,'2026-08-25 01:00:00');
        INSERT INTO execution_entity VALUES(2,'canonical',1,'manual','2026-08-25 02:00:00','2026-08-25 02:02:00','error',NULL,'2026-08-25 02:00:00');
        INSERT INTO execution_entity VALUES(3,'other',1,'webhook','2026-08-25 03:00:00','2026-08-25 03:02:00','success',NULL,'2026-08-25 03:00:00');
        """
    )
    connection.commit()
    connection.close()

    summary, daily = n8n_execution_metrics(
        "canonical", database_path=database, tz=ZoneInfo("America/New_York")
    )
    assert summary["executions"] == 2
    assert summary["successful"] == 1
    assert summary["failed"] == 1
    assert list(daily.index)[0].isoformat() == "2026-08-24"


def test_lifetime_totals_and_daily_history_use_persisted_evidence_and_local_days() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE source_runs(
          source_name TEXT,started_at TEXT,completed_at TEXT,request_count INTEGER,
          raw_count INTEGER,normalized_count INTEGER,duplicate_count INTEGER,
          eligible_count INTEGER,new_eligible_count INTEGER,reject_role_count INTEGER,
          reject_location_count INTEGER,reject_hard_requirement_count INTEGER,
          reject_company_count INTEGER,reject_other_targeting_count INTEGER,
          telegram_count INTEGER,downstream_success_count INTEGER,error_count INTEGER,
          run_status TEXT
        );
        CREATE TABLE jobs(first_seen_at TEXT,telegram_sent INTEGER,sent_to_n8n INTEGER);
        CREATE TABLE targeting_decisions(decided_at TEXT);
        CREATE TABLE telegram_delivery_claims(delivery_state TEXT,reserved_at TEXT,sent_at TEXT);
        INSERT INTO source_runs VALUES
          ('Alpha','2026-08-25 01:00:00','2026-08-25 01:05:00',2,100,90,5,3,3,70,5,4,2,1,2,1,0,'completed'),
          ('Beta','2026-08-25 05:00:00','2026-08-25 05:05:00',1,20,20,1,1,1,15,1,1,1,0,1,0,1,'degraded');
        INSERT INTO jobs VALUES('2026-08-25 02:00:00',1,0),('2026-08-25 06:00:00',-1,1);
        INSERT INTO targeting_decisions VALUES('2026-08-25 01:01:00'),('2026-08-25 05:01:00');
        INSERT INTO telegram_delivery_claims VALUES('sent','2026-08-25 02:10:00','2026-08-25 02:11:00');
        """
    )
    totals = lifetime_metrics(connection)
    daily = daily_history(connection, tz=ZoneInfo("America/New_York"))
    connection.close()

    assert totals["runs"] == 2
    assert totals["scanned"] == 120
    assert totals["rejected"] == 100
    assert totals["eligible"] == 4
    assert totals["jobs_stored"] == 2
    assert totals["jobs_delivered"] == 1
    assert totals["jobs_dispatched"] == 1
    assert totals["decisions"] == 2
    assert daily.loc[next(day for day in daily.index if day.isoformat() == "2026-08-24"), "Source runs"] == 1
    assert daily.loc[next(day for day in daily.index if day.isoformat() == "2026-08-25"), "Source runs"] == 1
