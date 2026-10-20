from __future__ import annotations

import builtins
import json
import urllib.request
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

from app import database, google_jobs_provider, randomized_source_runner as runner
from app.discovery_policy import DiscoveryPolicyError, require_free_provider, require_free_url
from app.source_readiness import source_readiness
from app.source_runtime_state_v1 import classify_row, runtime_snapshot


@pytest.mark.parametrize("provider", ["SerpAPI", "Apify", "Google Jobs/SerpAPI", "Apify Actor"])
def test_paid_names_block_even_when_marked_free(provider):
    with pytest.raises(DiscoveryPolicyError):
        require_free_provider(provider)


@pytest.mark.parametrize("cost", ["paid", "metered", "credits", "", "unknown", "free_trial"])
def test_unproven_cost_fails_closed(cost):
    with pytest.raises(DiscoveryPolicyError):
        require_free_provider("Fixture", cost_mode=cost)


@pytest.mark.parametrize("host", ["serpapi.com", "api.apify.com", "api.apify.net"])
def test_paid_endpoint_boundary(host):
    with pytest.raises(DiscoveryPolicyError):
        require_free_url("https://" + host + "/search")


def test_saved_paid_credentials_cannot_open_network_or_import_sdk(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Network or credential lookup reached")
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert not name.startswith(("serpapi", "apify"))
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setenv("SERPAPI_API_KEY", "fixture-paid-key-that-must-never-be-used")
    monkeypatch.setenv("APIFY_TOKEN", "fixture-paid-key-that-must-never-be-used")
    monkeypatch.setattr(requests, "get", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(google_jobs_provider, "discover_serpapi_key", forbidden)
    assert google_jobs_provider.provider_status()["serpapi_available"] is False
    with pytest.raises(DiscoveryPolicyError):
        google_jobs_provider.serpapi_google_jobs(query="HR", location="US")
    assert calls == []


def test_stale_database_registry_is_not_worker_evidence(hunter_db, monkeypatch):
    database.save_setting("source_worker_registry", {"workers": {"Rippling": "app.rippling_worker"}})
    assert runner.discover_worker("Rippling") is None
    monkeypatch.setattr(runner, "get_adapter_timer", lambda *a, **k: pytest.fail("reserved missing worker"))
    monkeypatch.setattr(runner, "_run_worker_command", lambda *a, **k: pytest.fail("started missing worker"))
    assert runner.run_one({"source_name": "Rippling", "cost_mode": "free"})["reason"] == "worker_missing"
    assert runner.run_one({"source_name": "Apify", "cost_mode": "free"})["reason"] == "disabled_by_policy"


def test_missing_worker_enablement_rejected_without_changing_history(hunter_db):
    c = database.get_connection()
    c.execute("INSERT INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode) VALUES('Rippling',1,0,60,'free')")
    c.commit(); c.close()
    with pytest.raises(ValueError, match="worker_missing"):
        database.save_source_policy("Rippling", enabled=True, cadence_minutes=60)
    c = database.get_connection()
    assert c.execute("SELECT enabled FROM source_health WHERE source_name='Rippling'").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM configuration_history WHERE setting_key='source_registry:Rippling'").fetchone()[0] == 0
    c.close()


def test_unavailable_source_is_setup_required_without_rewriting_run_facts(hunter_db):
    database.save_setting("source_worker_registry", {"workers": {"Rippling": "app.rippling_worker"}})
    c = database.get_connection()
    c.execute("INSERT INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode,health_status) VALUES('Rippling',1,1,60,'free','healthy')")
    c.commit(); c.close()
    row = next(x for x in runtime_snapshot(hunter_db) if x['source_name'] == 'Rippling')
    assert row['runtime_state'] == 'setup_required'
    assert row['health_status'] == 'healthy'  # historical fact is preserved
    assert runner.load_enabled_sources() == []


@pytest.mark.parametrize("module", ["app.missing_worker", "app...database", "app.database", "os", "app/../../os", "app.database;rm"])
def test_invalid_runtime_module_is_never_executable(module):
    assert not source_readiness({"source_name": "X", "cost_mode": "free"}, {"workers": {"X": module}})["ready"]


def test_google_configuration_has_only_free_provider():
    from pathlib import Path
    cfg = json.loads(Path("config/jobspy_boards.json").read_text())
    assert cfg["sources"]["google"]["provider_order"] == ["jobspy"]


def test_jobspy_failure_cannot_masquerade_as_empty_success():
    from app.free_adapter_policy_v1_1 import make_site_preserving_wrapper
    for bad in (lambda **k: None,):
        with pytest.raises(RuntimeError, match="returned None"):
            make_site_preserving_wrapper(bad)(site_name=["google"])
    def broken(**kwargs):
        raise TypeError("'NoneType' object is not subscriptable")
    with pytest.raises(RuntimeError, match="invalid NoneType"):
        make_site_preserving_wrapper(broken)(site_name=["indeed"])
    assert make_site_preserving_wrapper(lambda **k: [])(site_name=["google"]) == []


def test_failed_cohort_replay_excludes_unavailable_workers(hunter_db):
    from pathlib import Path
    evidence = json.loads((Path(__file__).parent / "fixtures/free_discovery_failure_cohort_20261020.json").read_text())
    registry = {"workers": {row['source_name']: row['registered_module'] for row in evidence['sources'] if row['registered_module']}}
    database.save_setting('source_worker_registry', registry)
    c = database.get_connection()
    for row in evidence['sources']:
        c.execute("INSERT OR REPLACE INTO source_health(source_name,source_tier,enabled,cadence_minutes,cost_mode,health_status) VALUES(?,?,?,60,?,?)",
                  (row['source_name'], row['source_tier'], row['enabled'], row['cost_mode'], row['health_status']))
    c.commit(); c.close()
    before = {row['source_name'] for row in evidence['sources'] if row['enabled'] and row['health_status'] == 'failed'}
    after = {row['source_name'] for row in runner.load_enabled_sources()}
    assert len(before) == 23
    assert before & after == {'Ashby'}
    assert len(before - after) == 22
    assert 'USAJobs' in after  # genuine existing module fallback survives


def test_retry_after_floor_includes_provider_error_evidence():
    assert runner._retry_after_floor({'errors': [{'retry_after_seconds': 28800}]}) == 28800
    assert runner._retry_after_floor({'discovery_summary': {'errors': [{'retry_after_seconds': '7200'}]}}) == 7200


@pytest.mark.parametrize('module', sorted(set(json.loads((Path(__file__).resolve().parents[1] / 'config/source_worker_registry.json').read_text())['workers'].values())))
def test_registered_cli_has_run_now_contract_without_initializing_database(module, tmp_path):
    sentinel = tmp_path / 'must-not-be-created.db'
    result = subprocess.run([sys.executable, '-m', module, '--help'],
                            env={**os.environ, 'DATABASE_PATH': str(sentinel)},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr[-1000:]
    assert '--run-now' in result.stdout
    assert not sentinel.exists()
