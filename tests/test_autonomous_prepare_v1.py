from __future__ import annotations

from app import autonomous_prepare_compat_v1 as compat
from app import autonomous_prepare_v1 as autonomous
from app import unified_hourly_coordinator as coordinator


class FakeConnection:
    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


def _plan(*, manual=None, auto=None, auto_limit=5, auto_used=0):
    return {
        "threshold": 95.0,
        "auto_limit": auto_limit,
        "product_volume_mode": "custom_limit",
        "enabled_lane_count": 1,
        "manual_limit": 25,
        "auto_used_today": auto_used,
        "manual_used_today": 0,
        "manual_candidates": list(manual or []),
        "auto_candidates": list(auto or []),
    }


def _job(job_id: int, *, title="People Analyst"):
    return {
        "id": job_id,
        "job_fingerprint": f"job-{job_id}",
        "company_name": "Acme",
        "title": title,
        "hunter_score": 99.0,
    }


def _native_routing():
    return {
        "engine": "NATIVE",
        "mode": "DURABLE_PREPARE",
        "durable_prepare_is_auto_owner": True,
        "legacy_auto_n8n_queue_enabled": False,
        "reason": "native_durable_prepare",
        "submission_authority": False,
    }


def _legacy_n8n_routing():
    return {
        "engine": "N8N",
        "mode": "LEGACY_N8N_COMPAT",
        "durable_prepare_is_auto_owner": False,
        "legacy_auto_n8n_queue_enabled": True,
        "reason": "n8n_durable_resume_binding_not_enabled",
        "submission_authority": False,
    }


def test_product_stage_contract_maps_prepare_states():
    assert autonomous.product_stage_for_state("QUEUED") == "OPTIMIZING"
    assert autonomous.product_stage_for_state("RUNNING") == "OPTIMIZING"
    assert autonomous.product_stage_for_state("NEEDS_INPUT") == "ISSUE"
    assert autonomous.product_stage_for_state("FAILED_SAFELY") == "ISSUE"
    assert autonomous.product_stage_for_state("READY_FOR_REVIEW") == "NEEDS REVIEW"
    assert autonomous.product_stage_for_state("READY_TO_SUBMIT") == "NEEDS REVIEW"


def test_n8n_durable_prepare_is_default_off(monkeypatch):
    monkeypatch.setattr(compat, "configured_engine", lambda: "N8N")
    monkeypatch.delenv(compat.N8N_DURABLE_PREPARE_ENV, raising=False)

    routing = compat.routing_mode()

    assert routing["mode"] == "LEGACY_N8N_COMPAT"
    assert routing["durable_prepare_is_auto_owner"] is False
    assert routing["legacy_auto_n8n_queue_enabled"] is True
    assert routing["submission_authority"] is False


def test_n8n_durable_prepare_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setattr(compat, "configured_engine", lambda: "N8N")
    monkeypatch.setenv(compat.N8N_DURABLE_PREPARE_ENV, "true")

    routing = compat.routing_mode()

    assert routing["mode"] == "DURABLE_PREPARE"
    assert routing["durable_prepare_is_auto_owner"] is True
    assert routing["legacy_auto_n8n_queue_enabled"] is False
    assert routing["submission_authority"] is False


def test_native_always_uses_durable_prepare(monkeypatch):
    monkeypatch.setattr(compat, "configured_engine", lambda: "NATIVE")
    monkeypatch.delenv(compat.N8N_DURABLE_PREPARE_ENV, raising=False)

    routing = compat.routing_mode()

    assert routing["mode"] == "DURABLE_PREPARE"
    assert routing["durable_prepare_is_auto_owner"] is True
    assert routing["legacy_auto_n8n_queue_enabled"] is False


def test_manual_queue_never_inserts_auto_candidate(monkeypatch):
    connection = FakeConnection()
    manual = _job(1)
    auto = _job(2)
    inserted = []

    monkeypatch.setattr(autonomous, "get_connection", lambda: connection)
    monkeypatch.setattr(autonomous, "ensure_n8n_schema", lambda _connection: None)
    monkeypatch.setattr(autonomous, "plan_candidates", lambda _connection: _plan(manual=[manual], auto=[auto]))
    monkeypatch.setattr(autonomous, "_begin_dispatch_transaction", lambda _connection: None)

    def insert(_connection, job, dispatch_mode, webhook_mode):
        inserted.append((int(job["id"]), dispatch_mode, webhook_mode))
        return {"id": 11, "queue_status": "pending"}

    monkeypatch.setattr(autonomous, "insert_queue_item", insert)

    result = autonomous.queue_manual_candidates(webhook_mode="production", dry_run=False)
    assert result["success"] is True
    assert inserted == [(1, "telegram_manual", "production")]
    assert [row["job_id"] for row in result["queued"]] == [1]


def test_auto_admission_skips_existing_and_obeys_combined_daily_limit(monkeypatch):
    connection = FakeConnection()
    jobs = [_job(10), _job(11), _job(12), _job(13)]
    admitted = []
    events = []

    monkeypatch.setattr(autonomous, "get_connection", lambda: connection)
    monkeypatch.setattr(autonomous, "ensure_n8n_schema", lambda _connection: None)
    monkeypatch.setattr(autonomous.orchestrator, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        autonomous,
        "plan_candidates",
        lambda _connection: _plan(auto=jobs, auto_limit=5, auto_used=1),
    )
    monkeypatch.setattr(autonomous, "_configured_engine", lambda _connection: "NATIVE")
    monkeypatch.setattr(autonomous, "_existing_prepare_job_ids", lambda _connection: {10})
    monkeypatch.setattr(autonomous, "_admissions_today", lambda _connection: 2)

    def start(job_id, *, engine):
        admitted.append((job_id, engine))
        return {"state": "QUEUED", "replayed": False}

    monkeypatch.setattr(autonomous.product_flow, "start_prepare_for_job", start)
    monkeypatch.setattr(
        autonomous,
        "_record_event",
        lambda _connection, **kwargs: events.append(kwargs),
    )

    result = autonomous.admit_auto_candidates(dry_run=False)
    assert admitted == [(11, "NATIVE"), (12, "NATIVE")]
    assert [row["job_id"] for row in result["admitted"]] == [11, 12]
    assert all(row["product_stage"] == "OPTIMIZING" for row in result["admitted"])
    assert [event["job_id"] for event in events] == [11, 12]


def test_auto_admission_isolates_one_job_failure(monkeypatch):
    connection = FakeConnection()
    jobs = [_job(20), _job(21)]
    events = []

    monkeypatch.setattr(autonomous, "get_connection", lambda: connection)
    monkeypatch.setattr(autonomous, "ensure_n8n_schema", lambda _connection: None)
    monkeypatch.setattr(autonomous.orchestrator, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        autonomous,
        "plan_candidates",
        lambda _connection: _plan(auto=jobs, auto_limit=None, auto_used=0),
    )
    monkeypatch.setattr(autonomous, "_configured_engine", lambda _connection: "NATIVE")
    monkeypatch.setattr(autonomous, "_existing_prepare_job_ids", lambda _connection: set())
    monkeypatch.setattr(autonomous, "_admissions_today", lambda _connection: 0)

    def start(job_id, *, engine):
        if job_id == 20:
            raise RuntimeError("fixture failure")
        return {"state": "QUEUED", "replayed": False}

    monkeypatch.setattr(autonomous.product_flow, "start_prepare_for_job", start)
    monkeypatch.setattr(
        autonomous,
        "_record_event",
        lambda _connection, **kwargs: events.append(kwargs),
    )

    result = autonomous.admit_auto_candidates(dry_run=False)
    assert result["success"] is False
    assert [row["job_id"] for row in result["errors"]] == [20]
    assert [row["job_id"] for row in result["admitted"]] == [21]
    assert {event["status"] for event in events} == {"failed", "admitted"}


def test_n8n_url_only_contract_becomes_issue_instead_of_trusted_ready(monkeypatch):
    operation_id = "operation-n8n"
    running = {
        "operation_id": operation_id,
        "engine": "N8N",
        "status": "RUNNING",
        "tasks": [
            {
                "task_id": "resume-task",
                "task_kind": "RESUME_PREPARE",
                "status": "PENDING",
            }
        ],
    }
    states = [
        dict(running),
        dict(running),
        {
            "operation_id": operation_id,
            "engine": "N8N",
            "status": "NEEDS_INPUT",
            "tasks": [],
        },
    ]
    calls = []

    monkeypatch.setattr(
        autonomous.orchestrator,
        "prepare_operation",
        lambda _operation_id: states.pop(0),
    )
    monkeypatch.setattr(
        autonomous.orchestrator,
        "claim_ready_tasks",
        lambda **kwargs: [{"task_id": "resume-task"}],
    )

    def needs_input(**kwargs):
        calls.append(kwargs)
        return {"operation_status": "NEEDS_INPUT"}

    monkeypatch.setattr(autonomous.orchestrator, "task_needs_input", needs_input)

    result = autonomous.drive_operation_once(operation_id)
    assert result["status"] == "NEEDS_INPUT"
    assert result["product_stage"] == "ISSUE"
    assert calls[0]["unresolved_keys"] == [
        "n8n.resume_truth_binding_contract_required"
    ]
    assert calls[0]["output"]["submission_authority"] is False


def test_coordinator_uses_manual_queue_plus_durable_auto_owner(monkeypatch):
    monkeypatch.setattr(coordinator, "manual_or_stored_worker_active", lambda: "")
    monkeypatch.setattr(coordinator, "load_enabled_sources", lambda: ["greenhouse"])
    monkeypatch.setattr(
        coordinator.autonomous_prepare_compat_v1,
        "routing_mode",
        _native_routing,
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_v1,
        "queue_manual_candidates",
        lambda **kwargs: {"success": True, "queued": [], "selected_count": 0},
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_admission_v1,
        "admit_auto_candidates",
        lambda **kwargs: {
            "success": True,
            "admitted": [{"job_id": 99, "product_stage": "OPTIMIZING"}],
            "errors": [],
        },
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_v1,
        "drive_open_operations",
        lambda: {
            "success": True,
            "operation_count": 1,
            "results": [{"operation_id": "op-99", "product_stage": "NEEDS REVIEW"}],
            "errors": [],
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        coordinator,
        "dispatch_pending",
        lambda **kwargs: {"success": True, "errors": [], "n8n_calls": 0, "dispatched": []},
    )
    monkeypatch.setattr(coordinator, "record_coordinator_event", lambda summary: None)

    result = coordinator.run_coordinator(
        skip_workers=True,
        force_workers=False,
        dry_run=False,
        webhook_mode="production",
        timeout_seconds=900,
    )
    assert result["success"] is True
    assert result["durable_prepare_is_auto_owner"] is True
    assert result["legacy_auto_n8n_queue_disabled_here"] is True
    assert result["auto_prepare_routing"]["engine"] == "NATIVE"
    assert result["autonomous_admission_result"]["admitted"][0]["product_stage"] == "OPTIMIZING"
    assert result["prepare_runtime_result"]["results"][0]["product_stage"] == "NEEDS REVIEW"


def test_coordinator_preserves_legacy_n8n_auto_path_by_default(monkeypatch):
    monkeypatch.setattr(coordinator, "manual_or_stored_worker_active", lambda: "")
    monkeypatch.setattr(coordinator, "load_enabled_sources", lambda: ["greenhouse"])
    monkeypatch.setattr(
        coordinator.autonomous_prepare_compat_v1,
        "routing_mode",
        _legacy_n8n_routing,
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_compat_v1,
        "queue_legacy_n8n_candidates",
        lambda **kwargs: {
            "success": True,
            "queued": [{"job_id": 77, "dispatch_mode": "auto_top_match"}],
            "selected_count": 1,
            "n8n_calls": 0,
            "submission_authority": False,
        },
    )

    def must_not_admit(**kwargs):
        raise AssertionError("durable auto admission must not run in N8N compatibility mode")

    monkeypatch.setattr(
        coordinator.autonomous_prepare_admission_v1,
        "admit_auto_candidates",
        must_not_admit,
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_v1,
        "queue_manual_candidates",
        must_not_admit,
    )
    monkeypatch.setattr(
        coordinator.autonomous_prepare_v1,
        "drive_open_operations",
        lambda: {
            "success": True,
            "operation_count": 0,
            "results": [],
            "errors": [],
            "submission_authority": False,
        },
    )
    monkeypatch.setattr(
        coordinator,
        "dispatch_pending",
        lambda **kwargs: {"success": True, "errors": [], "n8n_calls": 0, "dispatched": []},
    )
    monkeypatch.setattr(coordinator, "record_coordinator_event", lambda summary: None)

    result = coordinator.run_coordinator(
        skip_workers=True,
        force_workers=False,
        dry_run=False,
        webhook_mode="production",
        timeout_seconds=900,
    )

    assert result["success"] is True
    assert result["durable_prepare_is_auto_owner"] is False
    assert result["legacy_auto_n8n_queue_disabled_here"] is False
    assert result["auto_prepare_routing"]["mode"] == "LEGACY_N8N_COMPAT"
    assert result["queue_result"]["queued"][0]["dispatch_mode"] == "auto_top_match"
    assert result["autonomous_admission_result"]["skipped"] is True
    assert result["autonomous_admission_result"]["admitted"] == []


def test_native_worker_admits_scoped_consent_only_before_ats(monkeypatch):
    from app import native_standing_consent_v1 as standing
    from app import prepare_worker_adapters_v1a as v1a
    from app import prepare_worker_adapters_v1b as v1b
    from app import prepare_worker_adapters_v1c as v1c

    events = []
    operation = {
        "operation_id": "fresh-operation",
        "engine": "NATIVE",
        "status": "RUNNING",
        "tasks": [],
    }
    monkeypatch.setattr(
        autonomous.orchestrator,
        "prepare_operation", lambda oid:dict(operation),
    )
    for module, name in (
        (v1a, "run_native_resume_once"),
        (v1a, "run_recruiter_evidence_once"),
        (v1b, "run_answer_resolution_once"),
        (v1b, "run_cover_letter_decision_once"),
        (v1c, "run_application_preflight_once"),
        (v1c, "run_ats_form_preparation_once"),
    ):
        monkeypatch.setattr(module, name, lambda *, operation_id, worker_id, n=name: (
            events.append(n) or {"claimed":True}
        ))
    monkeypatch.setattr(standing, "status", lambda:{"enabled":True})
    monkeypatch.setattr(
        standing, "maybe_authorize_fresh_operation",
        lambda oid: events.append("bounded-consent") or {
            "authorized":True,"submission_authority":False
        },
    )
    result = autonomous.drive_operation_once("fresh-operation")
    assert result["engine"] == "NATIVE"
    assert result["submission_authority"] is False
    assert events.index("run_application_preflight_once") < events.index("bounded-consent")
    assert events.index("bounded-consent") < events.index("run_ats_form_preparation_once")


def test_native_worker_never_calls_standing_mandate_without_explicit_consent(monkeypatch):
    from app import native_standing_consent_v1 as standing
    from app import prepare_worker_adapters_v1a as v1a
    from app import prepare_worker_adapters_v1b as v1b
    from app import prepare_worker_adapters_v1c as v1c

    monkeypatch.setattr(
        autonomous.orchestrator, "prepare_operation",
        lambda oid:{"engine":"NATIVE","status":"RUNNING","tasks":[]},
    )
    monkeypatch.setattr(standing,"status",lambda:{"enabled":False})
    monkeypatch.setattr(
        standing,"maybe_authorize_fresh_operation",
        lambda oid:pytest.fail("No automatic authority when default off"),
    )
    for module, name in (
        (v1a, "run_native_resume_once"),
        (v1a, "run_recruiter_evidence_once"),
        (v1b, "run_answer_resolution_once"),
        (v1b, "run_cover_letter_decision_once"),
        (v1c, "run_application_preflight_once"),
        (v1c, "run_ats_form_preparation_once"),
    ):
        monkeypatch.setattr(module,name,lambda **kwargs:{"claimed":False})
    assert autonomous.drive_operation_once("old-operation")["submission_authority"] is False
