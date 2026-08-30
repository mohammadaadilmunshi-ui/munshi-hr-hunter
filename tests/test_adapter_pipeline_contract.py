from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app import database, dice_worker, jobspy_board_common, jobspy_pipeline, smartrecruiters_worker
from app.dashboard_adapter_sources_v2_3_1 import SOURCE_SPECS, make_job
from app.ats_common import parse_location
from app.dashboard_targeting_gate import filter_dashboard_jobs, record_source_metrics
from app.job_store import save_job
from app.targeting import filter_jobs


ROOT = Path(__file__).resolve().parent.parent


def test_description_sections_are_enriched_before_final_targeting(hunter_db) -> None:
    base = {
        "title": "Recruiting Coordinator",
        "company_name": "Fixture Employer",
        "location_raw": "Austin, TX",
        "country": "US",
    }
    required = filter_dashboard_jobs(
        [
            {
                **base,
                "description_raw": (
                    "Qualifications:\n"
                    "- 5+ years of recruiting coordination experience required."
                ),
            }
        ]
    )
    preferred = filter_dashboard_jobs(
        [
            {
                **base,
                "ats_job_id": "preferred-fixture",
                "source": "Fixture/ATS",
                "description_raw": (
                    "Preferred Qualifications:\n"
                    "- 5+ years of recruiting coordination experience preferred, not required."
                ),
            }
        ]
    )

    assert required["reject_hard_requirement"] == 1
    assert required["eligible"] == 0
    assert preferred["eligible"] == 1
    assert preferred["accounting_delta"] == 0

    connection = database.get_connection()
    try:
        stored = save_job(
            connection,
            preferred["eligible_jobs"][0],
            actor="fixture_worker",
        )
        connection.commit()
        row = connection.execute(
            "SELECT status,hard_rejection_reason,primary_decision FROM jobs WHERE id=?",
            (stored["job_id"],),
        ).fetchone()
    finally:
        connection.close()

    assert stored["inserted"] is True
    assert row["status"] == "found"
    assert row["hard_rejection_reason"] is None
    assert row["primary_decision"] == "ELIGIBLE"


def test_personio_provider_boundary_does_not_fabricate_us_country() -> None:
    job = make_job(
        SOURCE_SPECS["personio"],
        company="Fixture GmbH",
        title="HR Coordinator",
        location="Cologne",
        job_url="https://fixture.jobs.personio.de/job/1",
        description="On-site in Cologne under German labor law.",
    )

    result = filter_dashboard_jobs([job])

    assert job.get("country") is None
    assert result["eligible"] == 0
    assert result["reject_location"] == 1
    assert result["accounting_delta"] == 0


def test_source_stage_and_query_durations_are_truthful(hunter_db) -> None:
    database.save_setting(
        "provider_runtime",
        json.loads((ROOT / "config" / "provider_runtime_policy.json").read_text()),
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode
            ) VALUES ('Timing Fixture',1,1,180,'free')
            """
        )
        connection.commit()
    finally:
        connection.close()

    record_source_metrics(
        "Timing Fixture",
        raw_jobs=1,
        eligible_jobs=1,
        inserted_jobs=1,
        duplicate_jobs=0,
        provider_used="fixture",
        filter_summary={
            "run_id": "timing-fixture-run",
            "raw_normalized": 1,
            "telegram_messages": 1,
            "_stage_durations_ms": {"TARGET": 7.5},
            "_decision_rows": [{
                "run_id": "timing-fixture-run",
                "source_name": "Timing Fixture",
                "job_identity": "timing-fixture-job",
                "title": "People Analytics Analyst",
                "company_name": "Fixture Employer",
                "location_raw": "Austin, TX",
                "primary_category": "ELIGIBLE",
                "query_name": "Fixture query",
                "role_family": "HR CORE",
            }],
            "query_requests": [
                {
                    "query_name": "Fixture query",
                    "role_family": "HR CORE",
                    "requests": 1,
                    "raw": 1,
                    "errors": 0,
                    "duration_ms": 12.5,
                }
            ],
        },
    )

    connection = database.get_connection()
    try:
        stages = {
            row["stage"]: dict(row)
            for row in connection.execute(
                "SELECT stage,item_count,duration_ms,stage_status FROM source_run_stages "
                "WHERE run_id='timing-fixture-run'"
            )
        }
        query = connection.execute(
            "SELECT duration_ms,telegram_count,new_eligible_count FROM query_performance "
            "WHERE run_id='timing-fixture-run' AND query_name='Fixture query'"
        ).fetchone()
        source_run = connection.execute(
            "SELECT telegram_count,request_count FROM source_runs "
            "WHERE run_id='timing-fixture-run'"
        ).fetchone()
    finally:
        connection.close()

    assert stages["FETCH"]["duration_ms"] == 12.5
    assert stages["FETCH"]["stage_status"] == "completed"
    assert stages["TARGET"]["duration_ms"] == 7.5
    assert stages["TARGET"]["stage_status"] == "completed"
    assert stages["PERSIST"]["duration_ms"] >= 0
    assert stages["PERSIST"]["stage_status"] == "completed"
    assert stages["TELEGRAM"]["item_count"] == 1
    assert stages["NORMALIZE"]["duration_ms"] is None
    assert stages["NORMALIZE"]["stage_status"] == "completed_unmeasured"
    assert query["duration_ms"] == 12.5
    assert query["telegram_count"] == 1
    assert query["new_eligible_count"] == 1
    assert source_run["telegram_count"] == 1
    assert source_run["request_count"] == 1


def test_indeed_preserves_provider_fetch_count_when_targeting_input_is_capped(
    monkeypatch,
) -> None:
    raw_jobs = [
        {
            "source": "JobSpy/indeed",
            "title": "HR Coordinator",
            "company_name": f"Employer {index}",
            "location_raw": "Austin, TX",
            "country": "US",
            "_query_name": "HR Coordinator",
        }
        for index in range(3)
    ]
    monkeypatch.setattr(
        jobspy_board_common,
        "collect_jobspy_jobs",
        lambda **_kwargs: (
            raw_jobs,
            {
                "raw_jobs_found": 3,
                "query_requests": [{"query_name": "HR Coordinator", "requests": 1, "raw": 3}],
            },
        ),
    )
    monkeypatch.setattr(
        jobspy_board_common,
        "filter_dashboard_jobs",
        lambda jobs: {
            "eligible_jobs": list(jobs),
            "raw_normalized": len(jobs),
            "eligible": len(jobs),
            "accounting_delta": 0,
        },
    )

    jobs, summary = jobspy_board_common.collect_indeed_jobs(
        {"results_per_request": 20, "hours_old": 24, "max_raw_jobs": 2}
    )

    assert len(jobs) == 2
    assert summary["raw_jobs_found"] == 3
    assert summary["provider_raw_jobs_found"] == 3
    assert summary["raw_normalized"] == 2


def test_jobspy_query_attribution_separates_new_duplicates_and_backlog_sends() -> None:
    new, duplicates, inserted_queries = jobspy_board_common._query_storage_attribution(
        [
            {"_query_name": "HR Analyst"},
            {"_query_name": "HR Coordinator"},
            {"_query_name": "HR Coordinator"},
        ],
        [
            {"inserted": True, "job_id": 101},
            {"inserted": False, "job_id": 88},
            {"inserted": True, "job_id": 102},
        ],
    )
    telegram = {
        "sent": [
            {"job_id": 101},
            {"job_id": 102},
            {"job_id": 999},  # backlog from a different source/run
        ]
    }

    assert new == {"HR Analyst": 1, "HR Coordinator": 1}
    assert duplicates == {"HR Coordinator": 1}
    assert jobspy_board_common._query_telegram_attribution(
        telegram, inserted_queries
    ) == {"HR Analyst": 1, "HR Coordinator": 1}


def test_jobspy_dispatch_is_always_scoped_to_its_source(monkeypatch) -> None:
    captured = {}

    def _dispatch(**kwargs):
        captured.update(kwargs)
        return {"telegram_messages_sent": 0, "sent": []}

    monkeypatch.setattr(jobspy_board_common, "dispatch_unsent_jobs", _dispatch)
    monkeypatch.setattr(jobspy_board_common, "telegram_batch_limit", lambda: 7)

    jobspy_board_common._dispatch_for_source("JobSpy/indeed")

    assert captured == {"source_prefix": "JobSpy/indeed", "limit": 7}


def test_linkedin_rotation_records_query_request_provenance(monkeypatch) -> None:
    monkeypatch.setattr(
        jobspy_board_common,
        "_load_dashboard_targeting",
        lambda: (
            ["HR Operations Coordinator"],
            [{
                "rule_id": 7,
                "rule_name": "United States",
                "search_location": "United States",
                "remote_only": False,
            }],
        ),
    )
    monkeypatch.setattr(jobspy_board_common, "_state_cursors", lambda _key: (0, 0))
    monkeypatch.setattr(
        jobspy_board_common,
        "_scrape_jobs",
        lambda **_kwargs: [{
            "id": "fixture-linkedin-1",
            "site": "linkedin",
            "company": "Fixture Employer",
            "title": "HR Operations Coordinator",
            "location": "Austin, TX",
            "job_url": "https://example.test/linkedin/1",
        }],
    )
    monkeypatch.setattr(
        jobspy_board_common,
        "filter_dashboard_jobs",
        lambda raw_jobs, **_kwargs: (
            raw_jobs,
            {
                "run_id": "fixture-run",
                "raw_jobs_found": len(raw_jobs),
                "raw_normalized": len(raw_jobs),
                "eligible": len(raw_jobs),
                "accounting_delta": 0,
            },
        ),
    )

    jobs, summary = jobspy_board_common.collect_linkedin_jobs({
        "source_key": "linkedin_jobspy",
        "requests_per_run": 1,
        "results_per_request": 20,
        "hours_old": 72,
        "linkedin_fetch_description": True,
        "max_raw_jobs": 40,
    })

    assert len(jobs) == 1
    assert jobs[0]["_query_name"] == "HR Operations Coordinator"
    assert jobs[0]["_matched_rule_id"] == 7
    assert summary["request_count"] == 1
    assert summary["query_requests"][0]["requests"] == 1
    assert summary["query_requests"][0]["raw"] == 1
    assert summary["query_requests"][0]["duration_ms"] >= 0


def test_shared_board_normalizer_does_not_invent_us_country(hunter_db) -> None:
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
              source_name,source_tier,enabled,cadence_minutes,cost_mode
            ) VALUES ('Comeet',1,1,180,'free')
            """
        )
        connection.commit()
    finally:
        connection.close()
    job = make_job(
        SOURCE_SPECS["comeet"],
        company="Fixture Employer",
        title="People Operations Analyst",
        location="Not specified",
        job_url="https://example.test/jobs/unknown-location",
    )
    assert "country" not in job
    assert filter_jobs([job])["reject_location"] == 1


def test_shared_ats_location_parser_keeps_unknown_country_unknown() -> None:
    assert parse_location(None)["country"] is None
    assert parse_location("Remote")["country"] is None
    assert parse_location("Austin, TX")["country"] is None
    assert parse_location("Austin, TX, US")["country"] == "US"


def test_dice_keeps_canonical_provider_metadata_for_persistence(hunter_db) -> None:
    job = {
        "source": "Dice/direct",
        "title": "HR Coordinator",
        "company": "Fixture Staffing",
        "location": "Austin, TX, US",
        "description": "Coordinate onboarding and HR operations.",
        "job_url": "https://example.test/dice/fixture",
    }

    accepted, _reason, _role, _locations, category = dice_worker._dashboard_candidate_match(
        job, [], []
    )

    assert accepted is True
    assert job["company_name"] == "Fixture Staffing"
    assert job["location_raw"] == "Austin, TX, US"
    assert job["description_raw"].startswith("Coordinate onboarding")
    assert job["_targeting_decision"]["primary_category"] == "ELIGIBLE"
    assert category == "ELIGIBLE"


def test_dice_exclusive_metrics_record_decisions_and_within_run_duplicates(
    hunter_db,
) -> None:
    base = {
        "source": "Dice/direct",
        "title": "HR Coordinator",
        "company": "Fixture Staffing",
        "location": "Austin, TX, US",
        "description": "Coordinate onboarding and HR operations.",
        "job_url": "https://example.test/dice/fixture",
    }
    diagnostics = {
        "run_id": "dice-fixture-run",
        "canonical_primary_counts": {},
        "canonical_evaluations": 0,
    }
    first = dict(base)
    second = dict(base)
    dice_worker._dashboard_candidate_match(first, [], [])
    dice_worker._dashboard_candidate_match(second, [], [])

    first_category = dice_worker._count_canonical_result(
        diagnostics, "ELIGIBLE", first
    )
    second_category = dice_worker._count_canonical_result(
        diagnostics, "ELIGIBLE", second
    )

    assert first_category == "ELIGIBLE"
    assert second_category == "DUPLICATE"
    assert diagnostics["canonical_primary_counts"] == {
        "ELIGIBLE": 1,
        "DUPLICATE": 1,
    }
    assert [row["primary_category"] for row in diagnostics["_decision_rows"]] == [
        "ELIGIBLE",
        "DUPLICATE",
    ]


def test_jobspy_provider_collection_does_not_apply_private_targeting(monkeypatch, hunter_db) -> None:
    database.save_setting(
        "query_strategy",
        json.loads((ROOT / "config" / "query_strategy.json").read_text()),
        changed_by="pytest",
    )
    raw_jobs = [
        {
            "source": "JobSpy/indeed",
            "ats_job_id": "foreign-patient-role",
            "company_name": "Example Clinic",
            "title": "Patient Recruitment Coordinator",
            "location_raw": "Berlin, Germany",
            "country": "DE",
        }
    ]
    monkeypatch.setattr(jobspy_pipeline, "load_target_roles", lambda: ["Recruiting Coordinator"])
    monkeypatch.setattr(
        jobspy_pipeline,
        "select_queries",
        lambda _source_name: [
            {
                "family": "RECRUITING OPERATIONS",
                "query": "Recruiting Coordinator",
                "selection_mode": "fixture",
            }
        ],
    )
    monkeypatch.setattr(
        jobspy_pipeline,
        "build_location_search_plan",
        lambda: [
            {
                "rule_id": 1,
                "rule_name": "United States",
                "search_location": "United States",
                "remote_only": False,
            }
        ],
    )
    monkeypatch.setattr(
        jobspy_pipeline,
        "fetch_jobspy_jobs",
        lambda **_kwargs: {"success": True, "jobs": raw_jobs, "errors": []},
    )

    normalized, summary = jobspy_pipeline.collect_jobspy_jobs(
        sites=["indeed"], results_wanted=1, hours_old=24
    )

    assert normalized == raw_jobs
    assert summary["provider_policy_filtering"] is False
    assert summary["canonical_targeting_pending"] is True
    canonical = filter_jobs(normalized)
    assert canonical["primary_counts"]["REJECT_ROLE"] == 1
    assert canonical["accounting_delta"] == 0


class _NoopConnection:
    def commit(self) -> None:
        return None

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_smartrecruiters_extracts_detail_before_canonical_rejection(monkeypatch, hunter_db) -> None:
    database.save_setting(
        "downstream_contract",
        json.loads((ROOT / "config" / "downstream_contract.json").read_text()),
        changed_by="pytest",
    )
    posting = {
        "id": "patient-role-1",
        "name": "Patient Recruitment Coordinator",
        "company": {"identifier": "fixture", "name": "Fixture Clinic"},
        "location": {"country": "DE", "city": "Berlin", "remote": False},
    }
    detail_calls: list[str] = []
    monkeypatch.setattr(smartrecruiters_worker, "load_source_enabled", lambda: True)
    monkeypatch.setattr(
        smartrecruiters_worker,
        "load_companies",
        lambda _limit: [{"id": 1, "company_name": "Fixture Clinic", "board_token": "fixture"}],
    )
    monkeypatch.setattr(
        smartrecruiters_worker,
        "get_setting",
        lambda *_args, **_kwargs: {"max_detail_requests_per_board": 5},
    )
    monkeypatch.setattr(
        smartrecruiters_worker,
        "fetch_all_postings",
        lambda *_args, **_kwargs: {"postings": [posting], "pages": [{"response_ms": 1}]},
    )

    def _detail(_identifier: str, posting_id: str):
        detail_calls.append(posting_id)
        return {
            "posting": {
                **posting,
                "jobAd": {"sections": {"jobDescription": {"text": "Recruit patients for a clinical trial."}}},
            }
        }

    monkeypatch.setattr(smartrecruiters_worker, "fetch_posting_detail", _detail)
    monkeypatch.setattr(smartrecruiters_worker, "update_company_health", lambda *_a, **_k: None)
    monkeypatch.setattr(smartrecruiters_worker, "update_source_health", lambda *_a, **_k: None)
    monkeypatch.setattr(smartrecruiters_worker, "get_connection", lambda: _NoopConnection())
    monkeypatch.setattr(
        smartrecruiters_worker,
        "dispatch_unsent_jobs",
        lambda **_kwargs: {"telegram_messages_sent": 0, "errors": []},
    )
    monkeypatch.setattr(smartrecruiters_worker, "record_source_metrics", lambda *_a, **_k: None)
    monkeypatch.setattr(smartrecruiters_worker, "emit_source_run_result", lambda _payload: {})

    result = smartrecruiters_worker.run_worker(
        SimpleNamespace(max_companies=1, run_now=True, max_pages=1, telegram_limit=1)
    )

    assert detail_calls == ["patient-role-1"]
    assert result["provider_raw_jobs_found"] == 1
    assert result["normalized_jobs"] == 1
    assert result["excluded_by_role"] == 1
    assert result["unique_jobs_ready"] == 0
    assert result["accounting_delta"] == 0
