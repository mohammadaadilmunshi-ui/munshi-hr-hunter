from __future__ import annotations

import importlib
import inspect

from app import product_application_flow_v2 as flow


def test_extension_and_dashboard_read_one_redacted_provider_summary(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET", "synthetic-only-api-secret")
    api=importlib.import_module("app.api")
    monkeypatch.setattr(flow,"flow_for_job",lambda *_a,**_kw:{
        "provider_route":{
            "route_id":"route-synthetic","application_id":"application-synthetic",
            "provider":"TSENTA","state":"PROVIDER_SUBMITTED",
            "attempt_count":2,"updated_at":"2026-10-15T10:00:00+00:00",
            "private_email":"candidate@example.com",
            "provider_application_id":"private-provider-app",
        }
    })
    result=api.get_job_application_provider_status(7)
    assert result["job_id"]==7
    assert result["engine"]=="TSENTA"
    assert result["employer_receipt_confirmed"] is False
    assert result["submission_authority"] is False
    assert result["raw_candidate_data_included"] is False
    assert "candidate@example.com" not in str(result)
    assert "private-provider-app" not in str(result)


def test_shared_provider_summary_requires_independent_receipt_evidence(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET", "synthetic-only-api-secret")
    api=importlib.import_module("app.api")
    monkeypatch.setattr(flow,"flow_for_job",lambda *_a,**_kw:{
        "provider_route":{
            "route_id":"route-synthetic",
            "provider":"NATIVE","state":"EMPLOYER_CONFIRMED",
            "receipt_evidence_digest":"f"*64,
        }
    })
    result=api.get_job_application_provider_status(7)
    assert result["employer_receipt_confirmed"] is True
    assert result["status_label"]=="Employer confirmation verified"


def test_product_flow_selects_the_actual_receipt_digest_column():
    source=inspect.getsource(flow._provider_route)
    assert "receipt_evidence_digest" in source



def test_extension_application_lookup_returns_exact_sanitized_state(monkeypatch):
    monkeypatch.setenv("HUNTER_API_SECRET","synthetic-only-api-secret")
    api=importlib.import_module("app.api")
    from app import application_provider_router_v1 as router
    monkeypatch.setattr(router,"get_route_by_application_id",
        lambda _value:{
            "route_id":"route-safe",
            "application_id":"application-example",
            "job_id":45,
            "provider":"NATIVE",
            "state":"PROVIDER_SUBMITTED",
            "attempt_count":2,
            "updated_at":"2026-10-15T00:00:00Z",
            "receipt_evidence_digest":None,
            "private_response":"candidate-secret",
        })
    result=api.get_application_provider_status_by_application("application-example")
    assert result["employer_receipt_confirmed"] is False
    assert result["submission_authority"] is False
    assert result["application_id"]=="application-example"
    assert "candidate-secret" not in str(result)


def test_extension_lookup_missing_route_is_generic_404(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.setenv("HUNTER_API_SECRET","synthetic-only-api-secret")
    api=importlib.import_module("app.api")
    from app import application_provider_router_v1 as router
    monkeypatch.setattr(router,"get_route_by_application_id",lambda _:None)
    with pytest.raises(HTTPException) as error:
        api.get_application_provider_status_by_application("application-not-found")
    assert error.value.status_code == 404
    assert "not found" in error.value.detail.lower()



def test_status_bridge_secret_is_scoped_and_fail_closed(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.delenv("MUNSHI_HUNTER_STATUS_BRIDGE_SECRET", raising=False)
    api = importlib.import_module("app.api")
    with pytest.raises(HTTPException) as missing:
        api.require_status_bridge_secret("fake-but-long-secret-for-tests")
    assert missing.value.status_code == 401

    correct = "synthetic-status-only-" + "q" * 48
    monkeypatch.setenv("MUNSHI_HUNTER_STATUS_BRIDGE_SECRET", correct)
    api.require_status_bridge_secret(correct)
    with pytest.raises(HTTPException) as wrong:
        api.require_status_bridge_secret("synthetic-other-" + "q" * 48)
    assert wrong.value.status_code == 401

    from fastapi.routing import APIRoute
    route = next(
        value for value in api.app.routes
        if isinstance(value, APIRoute)
        and value.path == "/api/application-provider-status/by-application/{application_id}"
    )
    declared = {dependency.call.__name__ for dependency in route.dependant.dependencies}
    assert "require_status_bridge_secret" in declared
    assert "require_api_secret" not in declared



def test_bridge_secret_only_works_for_readonly_status_http_route(
    hunter_db, monkeypatch
):
    del hunter_db
    from fastapi.testclient import TestClient

    expected = "synthetic-specific-status-token-" + "z" * 48
    monkeypatch.setenv("MUNSHI_HUNTER_STATUS_BRIDGE_SECRET", expected)
    monkeypatch.setenv("HUNTER_API_SECRET", "fake-general-secret")
    api = importlib.import_module("app.api")
    client = TestClient(api.app)

    path = "/api/application-provider-status/by-application/application-no-route"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Hunter-Secret": expected}).status_code == 401

    authorized = client.get(
        path, headers={"X-Munshi-Status-Bridge-Secret": expected}
    )
    assert authorized.status_code == 404
    assert "not found" in authorized.json()["detail"].lower()

    # The status-only credential MUST NOT authorize general Hunter APIs.
    other = client.get(
        "/api/jobs/7/application-flow",
        headers={"X-Munshi-Status-Bridge-Secret": expected},
    )
    assert other.status_code == 401
