from __future__ import annotations

import json
from pathlib import Path

import requests

from app.employer_board_discovery import detect_candidate
from app.market_board_discovery import classify_url
from app.sources.public_ats import (
    PUBLIC_ATS_PROVIDER_NAMES,
    canonical_board_url,
    fetch_public_ats_board,
    provider_for_url,
)


SAMPLES = {'Naukri': 'https://www.naukri.com/job-listings-people-analytics-123', 'Rippling': 'https://ats.rippling.com/acme/jobs/123', 'iCIMS': 'https://careers-acme.icims.com/jobs/123/example/job', 'BrassRing': 'https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?jobid=123', 'JazzHR': 'https://acme.applytojob.com/apply/abc/people-analyst', 'Jobvite': 'https://jobs.jobvite.com/acme/job/o123', 'BreezyHR': 'https://acme.breezy.hr/p/abc-people-analyst', 'Oracle Recruiting': 'https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/123', 'Paylocity': 'https://recruiting.paylocity.com/recruiting/jobs/Details/123', 'UKG / UltiPro': 'https://recruiting.ultipro.com/ACM1000/JobBoard/jobdetails/123', 'Zoho Recruit': 'https://acme.zohorecruit.com/jobs/Careers/123', 'ADP Recruiting': 'https://workforcenow.adp.com/jobs/apply/posting.html?client=acme&jobId=123', 'Dover': 'https://jobs.dover.com/acme/jobs/123', 'Gem': 'https://jobs.gem.com/acme/jobs/123', 'SAP SuccessFactors': 'https://career5.successfactors.com/sfcareer/jobreqcareer?jobId=123', 'Teamtailor': 'https://acme.teamtailor.com/jobs/123-people-analyst', 'Polymer': 'https://jobs.polymer.co/acme/jobs/123', 'Phenom': 'https://jobs.phenom.com/job/123/people-analyst', 'Dayforce': 'https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/123', 'JOIN': 'https://join.com/companies/acme/123-people-analyst', 'CareerPlug': 'https://acme.careerplug.com/jobs/123', 'Hireology': 'https://careers.hireology.com/acme/123'}


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(
                f"{self.status_code} response",
                response=response,
            )


class FakeSession:
    def __init__(self, pages: dict[str, FakeResponse]):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return self.pages[url]


def runtime(**overrides):
    value = {
        "request_timeout_seconds": 2,
        "max_jobs_per_board": 10,
        "max_detail_requests_per_board": 10,
        "public_ats_browser_fallback_enabled": False,
    }
    value.update(overrides)
    return value


def test_all_22_public_ats_profiles_are_present_and_classified() -> None:
    assert len(PUBLIC_ATS_PROVIDER_NAMES) == 22
    assert set(PUBLIC_ATS_PROVIDER_NAMES) == set(SAMPLES)
    for provider, url in SAMPLES.items():
        assert provider_for_url(url) == provider
        assert classify_url(url)["provider"] == provider
        candidate = detect_candidate(
            url,
            company_hint="Acme",
            discovery_source="fixture",
        )
        assert candidate is not None
        assert candidate.company_name == "Acme"
        assert candidate.board_url == canonical_board_url(provider, url)


def test_jsonld_board_normalizes_jobs_without_bypassing_canonical_worker() -> None:
    url = SAMPLES["Teamtailor"]
    html = """
    <html><head>
    <script type="application/ld+json">
    {
      "@context":"https://schema.org",
      "@type":"JobPosting",
      "title":"People Analytics Specialist",
      "description":"<p>Build workforce dashboards</p>",
      "datePosted":"2026-09-21",
      "employmentType":"FULL_TIME",
      "identifier":{"@type":"PropertyValue","value":"TT-123"},
      "hiringOrganization":{"@type":"Organization","name":"Acme"},
      "jobLocation":{"address":{
        "addressLocality":"Philadelphia",
        "addressRegion":"PA",
        "addressCountry":"US"
      }},
      "url":"https://acme.teamtailor.com/jobs/123-people-analyst"
    }
    </script>
    </head></html>
    """
    result = fetch_public_ats_board(
        "Teamtailor",
        {"company_name": "Acme", "board_url": url},
        runtime(),
        session=FakeSession({url: FakeResponse(html)}),
    )
    assert result["requests"] == 1
    assert result["browser_renders"] == 0
    assert len(result["jobs"]) == 1
    job = result["jobs"][0]
    assert job["title"] == "People Analytics Specialist"
    assert job["company_name"] == "Acme"
    assert job["location_raw"] == "Philadelphia, PA, US"
    assert job["country"] == "US"
    assert job["ats_job_id"] == "TT-123"
    assert job["entry_path"] == "adapter_discovery"


def test_list_page_follows_bounded_job_links_and_normalizes_detail() -> None:
    board_url = "https://acme.breezy.hr/"
    detail_url = "https://acme.breezy.hr/p/abc-people-analyst"
    landing = '<html><body><a href="/p/abc-people-analyst">People</a></body></html>'
    detail = """
    <html><head><script type="application/ld+json">
    {"@type":"JobPosting","title":"HRIS Analyst",
      "hiringOrganization":{"name":"Acme"},
      "identifier":{"value":"BR-1"},
      "url":"https://acme.breezy.hr/p/abc-people-analyst"}
    </script></head></html>
    """
    session = FakeSession({
        board_url: FakeResponse(landing),
        detail_url: FakeResponse(detail),
    })
    result = fetch_public_ats_board(
        "BreezyHR",
        {"company_name": "Acme", "board_url": board_url},
        runtime(),
        session=session,
    )
    assert session.calls == [board_url, detail_url]
    assert result["requests"] == 2
    assert [job["title"] for job in result["jobs"]] == ["HRIS Analyst"]


def test_403_is_not_bypassed() -> None:
    url = SAMPLES["iCIMS"]
    try:
        fetch_public_ats_board(
            "iCIMS",
            {"company_name": "Acme", "board_url": url},
            runtime(),
            session=FakeSession({url: FakeResponse("denied", 403)}),
        )
    except requests.HTTPError as error:
        assert error.response is not None
        assert error.response.status_code == 403
    else:
        raise AssertionError("403 must remain a provider failure; no bypass is allowed")


def test_catalog_mapping_and_worker_registry_cover_every_provider() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "config/adapter_catalog.json").read_text())
    mapping = json.loads((root / "config/adapter_source_mapping.json").read_text())
    registry = json.loads((root / "config/source_worker_registry.json").read_text())
    catalog_by = {item["provider"]: item for item in catalog["providers"]}
    mapping_by = {item["provider"]: item for item in mapping["providers"]}
    for provider in PUBLIC_ATS_PROVIDER_NAMES:
        assert catalog_by[provider]["implemented"] is True
        assert catalog_by[provider]["support_level"] == "public_page_fallback"
        assert mapping_by[provider]["source_name"] == provider
        module = registry["workers"][provider]
        assert module == catalog_by[provider]["module"]
        assert (root / (module.replace(".", "/") + ".py")).is_file()


def test_migration_registers_22_independent_canonical_sources(hunter_db) -> None:
    import importlib.util

    from app import database

    root = Path(__file__).resolve().parents[1]
    migration_path = root / "migrations/058_public_ats_provider_adapters_v1.py"
    spec = importlib.util.spec_from_file_location("migration_058_public_ats", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    result = module.migrate()
    assert result["migration_id"] == "058_public_ats_provider_adapters_v1"
    assert len(result["providers"]) == 22
    assert result["enabled_by_migration"] is False

    connection = database.get_connection()
    try:
        placeholders = ",".join("?" for _ in PUBLIC_ATS_PROVIDER_NAMES)
        rows = connection.execute(
            f"""
            SELECT source_name,source_tier,enabled,cost_mode
            FROM source_health
            WHERE source_name IN ({placeholders})
            ORDER BY source_name
            """,
            tuple(PUBLIC_ATS_PROVIDER_NAMES),
        ).fetchall()
        coverage = connection.execute(
            f"""
            SELECT provider,implemented,fixture_tested,support_level,blocked_reason
            FROM adapter_coverage
            WHERE provider IN ({placeholders})
            ORDER BY provider
            """,
            tuple(PUBLIC_ATS_PROVIDER_NAMES),
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 22
    assert all(row["source_tier"] == 1 for row in rows)
    assert all(row["enabled"] == 0 for row in rows)
    assert all(row["cost_mode"] == "free" for row in rows)
    assert len(coverage) == 22
    assert all(row["implemented"] == 1 for row in coverage)
    assert all(row["fixture_tested"] == 1 for row in coverage)
    assert all(row["support_level"] == "public_page_fallback" for row in coverage)
    assert all(row["blocked_reason"] is None for row in coverage)

    registry = database.get_setting("source_worker_registry", {}) or {}
    for provider in PUBLIC_ATS_PROVIDER_NAMES:
        assert registry["workers"].get(provider)


def test_new_ats_source_uses_canonical_target_dedupe_metrics_pipeline(
    hunter_db,
    monkeypatch,
) -> None:
    from app import database
    from app import public_ats_worker

    root = Path(__file__).resolve().parents[1]
    runtime_policy = json.loads(
        (root / "config/provider_runtime_policy.json").read_text(encoding="utf-8")
    )
    runtime_policy.update({"retry_attempts": 1, "retry_backoff_seconds": 0})
    database.save_setting(
        "provider_runtime",
        runtime_policy,
        changed_by="pytest",
    )
    database.save_setting(
        "orchestration",
        {"maintenance_mode": False},
        changed_by="pytest",
    )

    board_url = "https://acme.teamtailor.com/jobs"
    job_url = "https://acme.teamtailor.com/jobs/fixture-people-analytics"
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
                source_name,source_tier,enabled,cadence_minutes,cost_mode
            ) VALUES('Teamtailor',1,1,360,'free')
            ON CONFLICT(source_name) DO UPDATE SET enabled=1
            """
        )
        connection.execute(
            """
            INSERT INTO adapter_coverage(
                provider,implementation_module,implemented,fixture_tested,
                enabled,health_status,support_level
            ) VALUES(
                'Teamtailor','app.teamtailor_worker',1,1,1,
                'fixture_tested','public_page_fallback'
            )
            ON CONFLICT(provider) DO UPDATE SET
                implemented=1,fixture_tested=1,enabled=1
            """
        )
        connection.execute(
            """
            INSERT INTO provider_board_registry(
                company_name,provider,board_url,careers_url,
                us_relevance,enabled,priority_weight
            ) VALUES(
                'Acme','Teamtailor',?,?, 'confirmed',1,10
            )
            """,
            (board_url, board_url),
        )
        connection.commit()
    finally:
        connection.close()

    def fake_fetch(provider, board, runtime, **_kwargs):
        assert provider == "Teamtailor"
        assert board["company_name"] == "Acme"
        return {
            "jobs": [
                {
                    "source": "Teamtailor/Acme",
                    "source_tier": 1,
                    "company_name": "Acme",
                    "title": "People Analytics Analyst",
                    "location_raw": "Philadelphia, PA",
                    "city": "Philadelphia",
                    "state": "PA",
                    "country": "US",
                    "remote_type": "Hybrid",
                    "employment_type": "Full-time",
                    "description_raw": (
                        "People analytics workforce dashboards, HR data, "
                        "reporting, and stakeholder analysis."
                    ),
                    "ats_job_id": "fixture-teamtailor-1",
                    "external_id": "fixture-teamtailor-1",
                    "apply_url": job_url,
                    "job_url": job_url,
                    "entry_path": "adapter_discovery",
                }
            ],
            "requests": 1,
            "duration_ms": 2.5,
        }

    monkeypatch.setattr(public_ats_worker, "fetch_public_ats_board", fake_fetch)

    first = public_ats_worker.run_public_ats("Teamtailor", max_boards=1)
    second = public_ats_worker.run_public_ats("Teamtailor", max_boards=1)

    assert first["success"] is True
    assert first["configured_boards"] == 1
    assert first["inserted"] == 1
    assert second["success"] is True
    assert second["inserted"] == 0
    assert second["database_duplicates"] >= 1

    connection = database.get_connection()
    try:
        job_count = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_url=?",
            (job_url,),
        ).fetchone()[0]
        source = connection.execute(
            """
            SELECT health_status,jobs_found_last_run,
                   eligible_jobs_last_run,duplicate_jobs_last_run,
                   provider_used_last_run
            FROM source_health
            WHERE source_name='Teamtailor'
            """
        ).fetchone()
        board = connection.execute(
            """
            SELECT health_status,last_job_count
            FROM provider_board_registry
            WHERE provider='Teamtailor'
            """
        ).fetchone()
        coverage = connection.execute(
            """
            SELECT live_tested,implemented,enabled
            FROM adapter_coverage
            WHERE provider='Teamtailor'
            """
        ).fetchone()
    finally:
        connection.close()

    assert job_count == 1
    assert source["health_status"] == "healthy"
    assert source["jobs_found_last_run"] == 1
    assert source["eligible_jobs_last_run"] == 0
    assert source["duplicate_jobs_last_run"] >= 1
    assert source["provider_used_last_run"] == "Teamtailor"
    assert board["health_status"] == "healthy"
    assert board["last_job_count"] == 1
    assert coverage["live_tested"] == 1
    assert coverage["implemented"] == 1
    assert coverage["enabled"] == 1


def test_known_job_urls_are_promoted_to_provider_board_roots() -> None:
    cases = {
        "Teamtailor": (
            "https://acme.teamtailor.com/jobs/123-people-analyst",
            "https://acme.teamtailor.com/jobs",
        ),
        "Jobvite": (
            "https://jobs.jobvite.com/acme/job/o123",
            "https://jobs.jobvite.com/acme/",
        ),
        "BreezyHR": (
            "https://acme.breezy.hr/p/abc-people-analyst",
            "https://acme.breezy.hr/",
        ),
        "Oracle Recruiting": (
            "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs/preview/123",
            "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs",
        ),
        "Dayforce": (
            "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL/jobs/123",
            "https://jobs.dayforcehcm.com/en-US/acme/CANDIDATEPORTAL",
        ),
        "CareerPlug": (
            "https://acme.careerplug.com/j/abc123",
            "https://acme.careerplug.com/",
        ),
        "Hireology": (
            "https://careers.hireology.com/acme/123/description",
            "https://careers.hireology.com/acme",
        ),
    }
    for provider, (job_url, expected) in cases.items():
        assert canonical_board_url(provider, job_url) == expected


def test_public_ats_worker_uses_canonical_provider_execution_path(
    hunter_db, monkeypatch
) -> None:
    from app import database
    from app import public_ats_worker as worker

    root = Path(__file__).resolve().parents[1]
    runtime_policy = json.loads(
        (root / "config/provider_runtime_policy.json").read_text()
    )
    runtime_policy.update(
        {
            "retry_attempts": 2,
            "retry_backoff_seconds": 0,
            "provider_board_run_limit": 10,
        }
    )
    database.save_setting(
        "provider_runtime",
        runtime_policy,
        changed_by="pytest",
    )
    database.save_setting(
        "orchestration",
        {"maintenance_mode": False},
        changed_by="pytest",
    )
    connection = database.get_connection()
    try:
        connection.execute(
            """
            INSERT INTO source_health(
                source_name,source_tier,enabled,cadence_minutes,cost_mode
            ) VALUES('Teamtailor',1,1,60,'free')
            """
        )
        connection.execute(
            """
            INSERT INTO adapter_coverage(
                provider,implementation_module,implemented,fixture_tested,
                enabled,health_status,support_level
            ) VALUES(
                'Teamtailor','app.teamtailor_worker',1,1,1,
                'fixture_tested','public_page_fallback'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO provider_board_registry(
                company_name,provider,board_url,careers_url,
                us_relevance,enabled,priority_weight
            ) VALUES(
                'Acme','Teamtailor','https://acme.teamtailor.com/jobs',
                'https://acme.teamtailor.com/jobs','confirmed',1,10
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    def fake_fetch(provider, board, runtime):
        assert provider == "Teamtailor"
        assert board["company_name"] == "Acme"
        assert runtime["retry_attempts"] == 2
        return {
            "jobs": [
                {
                    "source": "Teamtailor/Acme",
                    "source_tier": 1,
                    "company_name": "Acme",
                    "title": "People Analytics Analyst",
                    "ats_job_id": "TT-E2E-1",
                    "external_id": "TT-E2E-1",
                    "location_raw": "Philadelphia, PA",
                    "city": "Philadelphia",
                    "state": "PA",
                    "country": "US",
                    "remote_type": "Hybrid",
                    "date_posted": "2026-09-21",
                    "employment_type": "Full-time",
                    "description_raw": "People analytics dashboards and workforce reporting.",
                    "qualifications": "Excel and analytics",
                    "salary_raw": None,
                    "apply_url": "https://acme.teamtailor.com/jobs/tt-e2e-1",
                    "job_url": "https://acme.teamtailor.com/jobs/tt-e2e-1",
                    "_provider_country_raw": "US",
                    "entry_path": "adapter_discovery",
                }
            ],
            "requests": 1,
            "duration_ms": 4.0,
        }

    monkeypatch.setattr(worker, "fetch_public_ats_board", fake_fetch)
    result = worker.run_public_ats("Teamtailor", max_boards=1)
    assert result["success"] is True
    assert result["configured_boards"] == 1
    assert result["raw"] == 1
    assert result["eligible"] == 1
    assert result["inserted"] == 1

    connection = database.get_connection()
    try:
        job = connection.execute(
            "SELECT source,title,status FROM jobs WHERE ats_job_id='TT-E2E-1'"
        ).fetchone()
        health = connection.execute(
            """
            SELECT last_run_at,last_success_at,jobs_found_last_run,
                   raw_jobs_last_run,eligible_jobs_last_run,
                   inserted_jobs_last_run,health_status
            FROM source_health
            WHERE source_name='Teamtailor'
            """
        ).fetchone()
        coverage = connection.execute(
            """
            SELECT implemented,fixture_tested,live_tested,enabled,
                   health_status
            FROM adapter_coverage
            WHERE provider='Teamtailor'
            """
        ).fetchone()
    finally:
        connection.close()

    assert job is not None
    assert job["source"] == "Teamtailor/Acme"
    assert job["title"] == "People Analytics Analyst"
    assert health["last_run_at"] is not None
    assert health["last_success_at"] is not None
    assert health["jobs_found_last_run"] == 1
    assert health["raw_jobs_last_run"] == 1
    assert health["eligible_jobs_last_run"] == 1
    assert health["inserted_jobs_last_run"] == 1
    assert health["health_status"] == "healthy"
    assert coverage["implemented"] == 1
    assert coverage["fixture_tested"] == 1
    assert coverage["live_tested"] == 1
    assert coverage["enabled"] == 1
    assert coverage["health_status"] == "healthy"


def test_discovered_public_ats_board_requires_validation_before_runtime_promotion(
    hunter_db, monkeypatch
) -> None:
    from app import database
    from app import employer_board_discovery as discovery

    connection = database.get_connection()
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ats_company_registry(
                company_name TEXT,
                careers_url TEXT,
                enabled INTEGER DEFAULT 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO ats_company_registry(company_name,careers_url,enabled)
            VALUES(
                'Acme',
                'https://acme.teamtailor.com/jobs/123-people-analyst',
                1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_health(
                source_name,source_tier,enabled,cadence_minutes,cost_mode,
                health_status
            ) VALUES(
                'Teamtailor',1,0,360,'free','installed_disabled'
            )
            """
        )
        connection.commit()

        local = discovery.discover_local_candidates(connection)
        connection.commit()
        assert local["detected"]["teamtailor"] == 1
        before = connection.execute(
            """
            SELECT COUNT(*) FROM provider_board_registry
            WHERE provider='Teamtailor'
            """
        ).fetchone()[0]
        assert before == 0
    finally:
        connection.close()

    class Response:
        status_code = 200

    html = b"<html><body><a href=\"/jobs/456-new-people-analytics\">People Analytics</a></body></html>"

    def fake_request(_session, url, **_kwargs):
        assert url == "https://acme.teamtailor.com/jobs"
        return Response(), html

    monkeypatch.setattr(discovery, "_request_limited", fake_request)

    connection = database.get_connection()
    try:
        validated = discovery.validate_pending_candidates(
            connection,
            object(),
            max_candidates=10,
            request_delay_seconds=0.25,
            providers={"teamtailor"},
        )
        connection.commit()
        assert validated["validated"] == 1
        assert validated["valid"] == 1
        assert validated["unresolved"] == 0
    finally:
        connection.close()

    synced = discovery.sync_validated_boards_to_runtime(
        providers={"teamtailor"}
    )
    assert synced["inserted"] == 1

    connection = database.get_connection()
    try:
        board = connection.execute(
            """
            SELECT provider,company_name,board_url,enabled
            FROM provider_board_registry
            WHERE provider='Teamtailor'
            """
        ).fetchone()
        source = connection.execute(
            """
            SELECT enabled,health_status
            FROM source_health
            WHERE source_name='Teamtailor'
            """
        ).fetchone()
    finally:
        connection.close()

    assert board is not None
    assert board["company_name"] == "Acme"
    assert board["board_url"] == "https://acme.teamtailor.com/jobs"
    assert board["enabled"] == 1
    # Discovery owns board availability only; it must not silently change
    # the canonical source on/off policy.
    assert source["enabled"] == 0
    assert source["health_status"] == "configured_disabled"


def test_public_ats_validation_does_not_promote_zero_signal_page(
    hunter_db, monkeypatch
) -> None:
    from app import database
    from app import employer_board_discovery as discovery

    connection = database.get_connection()
    try:
        discovery.initialize_tables(connection)
        connection.execute(
            """
            INSERT INTO employer_board_discovery_candidates(
                provider,company_name,board_locator,board_url,raw_url,
                discovery_source,source_kind,validation_status,enabled
            ) VALUES(
                'teamtailor','Acme','acme.teamtailor.com',
                'https://acme.teamtailor.com/jobs',
                'https://acme.teamtailor.com/jobs',
                'fixture','career_page','pending',1
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    class Response:
        status_code = 200

    monkeypatch.setattr(
        discovery,
        "_request_limited",
        lambda *_args, **_kwargs: (
            Response(),
            b"<html><body><h1>Careers</h1><a href='/about'>About</a></body></html>",
        ),
    )

    connection = database.get_connection()
    try:
        result = discovery.validate_pending_candidates(
            connection,
            object(),
            max_candidates=10,
            request_delay_seconds=0.25,
            providers={"teamtailor"},
        )
        connection.commit()
        row = connection.execute(
            """
            SELECT validation_status,visible_jobs
            FROM employer_board_discovery_candidates
            WHERE provider='teamtailor'
            """
        ).fetchone()
    finally:
        connection.close()

    assert result["valid"] == 0
    assert result["unresolved"] == 1
    assert row["validation_status"] == "unresolved"
    assert row["visible_jobs"] == 0


def test_recaptcha_script_reference_is_not_a_visible_block_challenge() -> None:
    from app.sources.public_ats import inspect_public_page

    html = """
    <html><head>
      <script src="https://example.test/recaptcha.js"></script>
    </head><body>
      <a href="/jobs/123-people">People Analyst</a>
    </body></html>
    """
    result = inspect_public_page(
        "Teamtailor",
        "https://acme.teamtailor.com/jobs",
        html,
    )
    assert result["blocked"] is False
    assert result["job_links"] == 1


def test_visible_human_verification_is_still_blocked() -> None:
    from app.sources.public_ats import inspect_public_page

    result = inspect_public_page(
        "Teamtailor",
        "https://acme.teamtailor.com/jobs",
        "<html><body><h1>Verify you are human</h1></body></html>",
    )
    assert result["blocked"] is True


def test_js_detail_page_uses_bounded_browser_fallback(monkeypatch) -> None:
    from app.sources import public_ats as module

    board_url = "https://ats.rippling.com/acme/jobs"
    detail_url = "https://ats.rippling.com/acme/jobs/123"
    landing = '<html><body><a href="/acme/jobs/123">Open role</a></body></html>'
    shell = "<html><body><div id='root'></div></body></html>"
    rendered = """
    <html><head><script type="application/ld+json">
    {
      "@type":"JobPosting",
      "title":"People Operations Analyst",
      "identifier":{"value":"RIP-123"},
      "hiringOrganization":{"name":"Acme"},
      "jobLocation":{"address":{
        "addressLocality":"Philadelphia",
        "addressRegion":"PA",
        "addressCountry":"US"
      }},
      "url":"/acme/jobs/123"
    }
    </script></head></html>
    """
    session = FakeSession(
        {
            board_url: FakeResponse(landing),
            detail_url: FakeResponse(shell),
        }
    )

    def fake_render(url, _runtime):
        assert url == detail_url
        return rendered

    monkeypatch.setattr(module, "_render_page", fake_render)
    result = module.fetch_public_ats_board(
        "Rippling",
        {"company_name": "Acme", "board_url": board_url},
        runtime(max_detail_requests_per_board=1),
        session=session,
    )
    assert result["browser_renders"] == 1
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["title"] == "People Operations Analyst"
    assert result["jobs"][0]["job_url"] == detail_url


def test_validation_uses_render_when_static_page_has_no_job_signal(
    hunter_db, monkeypatch
) -> None:
    from app import employer_board_discovery as discovery

    class Response:
        status_code = 200

    static = b"<html><body><div id='app'></div></body></html>"
    rendered = (
        "<html><body>"
        "<a href='/jobs/123-people-analytics'>People Analytics</a>"
        "</body></html>"
    )
    monkeypatch.setattr(
        discovery,
        "_request_limited",
        lambda *_args, **_kwargs: (Response(), static),
    )
    monkeypatch.setattr(
        discovery,
        "render_public_page",
        lambda *_args, **_kwargs: rendered,
    )
    result = discovery.validate_candidate(
        object(),
        {
            "provider": "teamtailor",
            "company_name": "Acme",
            "board_url": "https://acme.teamtailor.com/jobs",
            "raw_url": "https://acme.teamtailor.com/jobs",
        },
    )
    assert result["valid"] is True
    assert result["visible_jobs"] == 1


def test_public_ats_fetch_rejects_mismatched_provider_host() -> None:
    try:
        fetch_public_ats_board(
            "iCIMS",
            {
                "company_name": "Acme",
                "board_url": "https://jobs.example.com/jobs",
            },
            runtime(),
            session=FakeSession({}),
        )
    except ValueError as error:
        assert "outside the provider allowlist" in str(error)
    else:
        raise AssertionError("provider-host mismatch must be rejected")


def test_fallback_html_rejects_template_and_generic_titles() -> None:
    from app.sources.public_ats import _fallback_html_job

    board = {"company_name": "Acme"}
    bad_titles = [
        "{{welcomeTitle.replace('[FIRSTNAME]', 'x')}}",
        "Acme",
        "Recommended Jobs",
        "Careers",
        "Open Positions",
    ]
    for title in bad_titles:
        html = f"<html><body><h1>{title}</h1><main>Some content</main></body></html>"
        assert (
            _fallback_html_job(
                "Teamtailor",
                board,
                "https://acme.teamtailor.com/jobs/123",
                html,
            )
            is None
        )

    good = _fallback_html_job(
        "Teamtailor",
        board,
        "https://acme.teamtailor.com/jobs/123",
        "<html><body><h1>People Analytics Analyst</h1><main>Role</main></body></html>",
    )
    assert good is not None
    assert good["title"] == "People Analytics Analyst"


def test_migration_registry_resolves_each_source_to_dedicated_worker(
    hunter_db,
) -> None:
    import importlib.util

    from app.randomized_source_runner import discover_worker

    root = Path(__file__).resolve().parents[1]
    migration_path = root / "migrations/058_public_ats_provider_adapters_v1.py"
    spec = importlib.util.spec_from_file_location(
        "migration_058_public_ats_scheduler",
        migration_path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.migrate()

    expected = {
        "Naukri": "app.naukri_worker",
        "Rippling": "app.rippling_worker",
        "iCIMS": "app.icims_worker",
        "BrassRing": "app.brassring_worker",
        "JazzHR": "app.jazzhr_worker",
        "Jobvite": "app.jobvite_worker",
        "BreezyHR": "app.breezyhr_worker",
        "Oracle Recruiting": "app.oracle_recruiting_worker",
        "Paylocity": "app.paylocity_worker",
        "UKG / UltiPro": "app.ukg_ultipro_worker",
        "Zoho Recruit": "app.zoho_recruit_worker",
        "ADP Recruiting": "app.adp_recruiting_worker",
        "Dover": "app.dover_worker",
        "Gem": "app.gem_worker",
        "SAP SuccessFactors": "app.sap_successfactors_worker",
        "Teamtailor": "app.teamtailor_worker",
        "Polymer": "app.polymer_worker",
        "Phenom": "app.phenom_worker",
        "Dayforce": "app.dayforce_worker",
        "JOIN": "app.join_worker",
        "CareerPlug": "app.careerplug_worker",
        "Hireology": "app.hireology_worker",
    }

    assert set(expected) == set(PUBLIC_ATS_PROVIDER_NAMES)
    for source_name, worker_module in expected.items():
        resolved = discover_worker(source_name)
        assert resolved == worker_module
        assert resolved != "app.schema_jobposting_worker"
        assert (root / (worker_module.replace(".", "/") + ".py")).is_file()
